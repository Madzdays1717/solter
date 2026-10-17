"""SQLite state: the reservoir of launches, live flow, calls and their measured outcomes."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .events import Trade

SCHEMA = '''
CREATE TABLE IF NOT EXISTS tokens (
  mint TEXT PRIMARY KEY,
  symbol TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '', uri TEXT,
  creator TEXT, created_at REAL NOT NULL,
  stage TEXT NOT NULL DEFAULT 'new',          -- new | migrated
  state TEXT NOT NULL DEFAULT 'fresh',        -- fresh | alive | dead | watching | called | expired
  account TEXT NOT NULL DEFAULT '',           -- bonding curve, or PumpSwap pool after migration
  dev_sol REAL, dev_tokens REAL,
  checks INTEGER NOT NULL DEFAULT 0, checked_at REAL, stalls INTEGER NOT NULL DEFAULT 0,
  real_sol REAL, peak_real_sol REAL, traction REAL NOT NULL DEFAULT 0, progress REAL,
  mcap_sol REAL, first_mcap_sol REAL, peak_mcap_sol REAL,
  migrated_at REAL, promotions INTEGER NOT NULL DEFAULT 0, cooldown_until REAL NOT NULL DEFAULT 0,
  watch_started REAL, watch_until REAL, last_trade_at REAL, last_sig TEXT,
  first_price REAL, last_price REAL,
  radar_seen_at REAL, radar_mc_usd REAL, radar_volume_5m_usd REAL,
  reason TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS tokens_state ON tokens(state, created_at);
CREATE INDEX IF NOT EXISTS tokens_creator ON tokens(creator);
CREATE TABLE IF NOT EXISTS trades (
  signature TEXT NOT NULL, idx INTEGER NOT NULL, mint TEXT NOT NULL, at REAL NOT NULL,
  slot INTEGER, wallet TEXT NOT NULL, side TEXT NOT NULL, tokens REAL NOT NULL,
  sol REAL, price REAL, src TEXT NOT NULL,
  PRIMARY KEY(signature, idx, mint));
CREATE INDEX IF NOT EXISTS trades_mint ON trades(mint, at);
CREATE TABLE IF NOT EXISTS processed (
  signature TEXT NOT NULL, mint TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY(signature, mint));
CREATE INDEX IF NOT EXISTS processed_at ON processed(at);
CREATE TABLE IF NOT EXISTS creators (
  creator TEXT PRIMARY KEY, launches INTEGER NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL);
CREATE TABLE IF NOT EXISTS calls (
  mint TEXT PRIMARY KEY, at REAL NOT NULL, state TEXT NOT NULL, stage TEXT NOT NULL,
  message_id INTEGER, symbol TEXT, name TEXT, score INTEGER, grade TEXT,
  price_sol REAL, mcap_sol REAL, sol_usd REAL, progress REAL, net_sol REAL, buyers INTEGER,
  top10 REAL, dev_pct REAL, launches INTEGER, socials INTEGER, note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS calls_at ON calls(at);
CREATE TABLE IF NOT EXISTS outcomes (
  mint TEXT PRIMARY KEY, call_at REAL NOT NULL, entry REAL NOT NULL,
  last REAL NOT NULL, last_at REAL NOT NULL, peak REAL NOT NULL, peak_at REAL NOT NULL,
  low REAL NOT NULL, r5 REAL, r15 REAL, r30 REAL, r60 REAL,
  milestone REAL NOT NULL DEFAULT 1, dev_sold INTEGER NOT NULL DEFAULT 0,
  dev_alerted INTEGER NOT NULL DEFAULT 0, gap INTEGER NOT NULL DEFAULT 0,
  done INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS spend (
  day TEXT NOT NULL, kind TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(day, kind));
DROP TABLE IF EXISTS budget;
CREATE TABLE IF NOT EXISTS radar_alerts (
  mint TEXT PRIMARY KEY, at REAL NOT NULL, state TEXT NOT NULL,
  market_cap_usd REAL NOT NULL, volume_5m_usd REAL NOT NULL);
CREATE INDEX IF NOT EXISTS radar_alerts_at ON radar_alerts(at);
CREATE TABLE IF NOT EXISTS metrics (key TEXT PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0);
'''

HORIZONS = (5, 15, 30, 60)   # minutes after a call at which its return is recorded


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=10000')
        self.db.executescript(SCHEMA)
        # Upgrade existing v2.0.2 databases in place without dropping history.
        existing = {r['name'] for r in self.db.execute('PRAGMA table_info(tokens)')}
        for name in ('radar_seen_at', 'radar_mc_usd', 'radar_volume_5m_usd'):
            if name not in existing:
                self.db.execute(f'ALTER TABLE tokens ADD COLUMN {name} REAL')
        self.db.commit()
        self.columns = {r['name'] for r in self.db.execute('PRAGMA table_info(tokens)')}
        self.outcome_columns = {r['name'] for r in self.db.execute('PRAGMA table_info(outcomes)')}

    def close(self):
        self.db.close()

    def _rows(self, sql: str, args=()) -> list[dict]:
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    # metrics -------------------------------------------------------------
    def metric(self, key: str, n: int = 1):
        self.db.execute('INSERT INTO metrics(key, n) VALUES(?, ?) '
                        'ON CONFLICT(key) DO UPDATE SET n = n + excluded.n', (key[:80], n))
        self.db.commit()

    def metrics(self) -> dict:
        return {r['key']: r['n'] for r in self.db.execute('SELECT key, n FROM metrics ORDER BY key')}

    # reservoir -----------------------------------------------------------
    def discover(self, info: dict, account: str, now: float) -> bool:
        cur = self.db.execute(
            '''INSERT OR IGNORE INTO tokens(mint, symbol, name, uri, creator, created_at, account,
               dev_sol, dev_tokens) VALUES(?,?,?,?,?,?,?,?,?)''',
            (info['mint'], info.get('symbol') or '', info.get('name') or '', info.get('uri'),
             info.get('creator'), now, account, info.get('dev_sol'), info.get('dev_tokens')))
        if cur.rowcount and info.get('creator'):
            self.db.execute('''INSERT INTO creators(creator, launches, first_seen, last_seen)
                VALUES(?, 1, ?, ?) ON CONFLICT(creator) DO UPDATE SET
                launches = launches + 1, last_seen = excluded.last_seen''',
                            (info['creator'], now, now))
        self.db.commit()
        return cur.rowcount == 1

    def launches(self, creator: str | None) -> int:
        """Launches this installation saw from `creator` (0 = unknown creator)."""
        if not creator:
            return 0
        row = self.db.execute('SELECT launches FROM creators WHERE creator=?', (creator,)).fetchone()
        return row['launches'] if row else 0

    def token(self, mint: str) -> dict | None:
        row = self.db.execute('SELECT * FROM tokens WHERE mint=?', (mint,)).fetchone()
        return dict(row) if row else None

    def update(self, mint: str, **fields):
        fields = {k: v for k, v in fields.items() if k in self.columns and k != 'mint'}
        if not fields:
            return
        assignments = ', '.join(f'{k}=?' for k in fields)
        self.db.execute(f'UPDATE tokens SET {assignments} WHERE mint=?', (*fields.values(), mint))
        self.db.commit()

    def migrate(self, mint: str, pool: str, now: float) -> str:
        """Point a token at its PumpSwap pool. Returns what the token was doing."""
        token = self.token(mint)
        if token and token['stage'] == 'migrated':
            return 'repeat'
        if token is None:
            self.db.execute('''INSERT INTO tokens(mint, created_at, stage, state, account, migrated_at)
                VALUES(?, ?, 'migrated', 'alive', ?, ?)''', (mint, now, pool, now))
            self.db.commit()
            return 'unknown'
        was = token['state']
        # A called token is tracked, never offered for a second call.
        state = was if was in ('watching', 'called') or self.has_call(mint) else 'alive'
        self.db.execute('''UPDATE tokens SET stage='migrated', state=?, account=?, migrated_at=?,
            first_price=NULL, last_sig=NULL, cooldown_until=0, promotions=0, reason=''
            WHERE mint=?''', (state, pool, now, mint))
        if was == 'watching':
            self.db.execute('DELETE FROM trades WHERE mint=?', (mint,))   # a new market starts
        self.db.commit()
        return was

    def sweep_batch(self, now: float, limit: int, first_check: float, recheck: float) -> list[dict]:
        """First-check FIFO prevents busy launches from starving older unchecked coins."""
        new = self._rows("""SELECT * FROM tokens WHERE state='fresh' AND checks=0 AND created_at<=?
            ORDER BY created_at ASC LIMIT ?""", (now - first_check, limit))
        alive = self._rows("""SELECT * FROM tokens WHERE state='alive' AND stage='new'
            ORDER BY checked_at ASC LIMIT ?""", (limit,))
        quiet = self._rows("""SELECT * FROM tokens WHERE state='fresh' AND checks>0 AND checked_at<=?
            ORDER BY checked_at ASC LIMIT ?""", (now - recheck, limit))
        batch, seen = [], set()
        for row in new + alive + quiet:
            if row['mint'] not in seen and row['account'] and len(batch) < limit:
                batch.append(row)
                seen.add(row['mint'])
        return batch

    def radar_batch(self, now: float, limit: int, minutes: int) -> list[dict]:
        """Fair round robin; independent of Solana log subscription slots."""
        return self._rows("""SELECT * FROM tokens
            WHERE state IN ('fresh', 'alive', 'watching', 'dead')
            AND COALESCE(migrated_at, created_at) >= ?
            AND mint NOT IN (SELECT mint FROM radar_alerts)
            ORDER BY COALESCE(radar_seen_at, 0) ASC, created_at DESC LIMIT ?""",
                          (now - minutes * 60, limit))

    def radar_mark(self, mint: str, at: float, mc: float | None, volume: float | None):
        self.db.execute("""UPDATE tokens SET radar_seen_at=?,
            radar_mc_usd=COALESCE(?, radar_mc_usd),
            radar_volume_5m_usd=COALESCE(?, radar_volume_5m_usd)
            WHERE mint=?""", (at, mc, volume, mint))
        self.db.commit()

    def radar_claim(self, mint: str, now: float, limit: int, mc: float, volume: float) -> bool:
        """Persist deduplication BEFORE send, even if Telegram times out."""
        count = self.db.execute('SELECT COUNT(*) FROM radar_alerts WHERE at>?',
                                (now - 3600,)).fetchone()[0]
        if count >= limit:
            return False
        cur = self.db.execute("""INSERT OR IGNORE INTO radar_alerts
            (mint, at, state, market_cap_usd, volume_5m_usd)
            VALUES(?, ?, 'attempting', ?, ?)""", (mint, now, mc, volume))
        self.db.commit()
        return cur.rowcount == 1

    def radar_finish(self, mint: str, state: str):
        self.db.execute('UPDATE radar_alerts SET state=? WHERE mint=?', (state, mint))
        self.db.commit()

    def alive(self) -> list[dict]:
        return self._rows("SELECT * FROM tokens WHERE state='alive' ORDER BY traction DESC LIMIT 500")

    def watching(self) -> list[dict]:
        return self._rows("SELECT * FROM tokens WHERE state='watching' ORDER BY watch_started")

    def subscriptions(self) -> list[dict]:
        """Accounts whose logs are needed: watched tokens and calls still being tracked."""
        return self._rows('''SELECT t.* FROM tokens t WHERE t.account != '' AND (t.state='watching'
            OR (t.state='called' AND EXISTS(SELECT 1 FROM outcomes o WHERE o.mint=t.mint AND o.done=0)))''')

    def promote(self, mint: str, now: float, watch_seconds: int) -> bool:
        cur = self.db.execute('''UPDATE tokens SET state='watching', promotions=promotions+1,
            watch_started=?, watch_until=?, first_price=NULL, last_trade_at=NULL, reason=''
            WHERE mint=? AND state='alive' ''', (now, now + watch_seconds, mint))
        self.db.commit()
        return cur.rowcount == 1

    def demote(self, mint: str, now: float, cooldown: float, reason: str):
        self.db.execute('''UPDATE tokens SET state='alive', cooldown_until=?, reason=?
            WHERE mint=? AND state='watching' ''', (now + cooldown, reason, mint))
        self.db.commit()

    def expire(self, now: float, reservoir_minutes: int) -> int:
        cur = self.db.execute('''UPDATE tokens SET state='expired',
            reason=CASE WHEN checks=0 THEN 'not checked in time' ELSE reason END
            WHERE (state IN ('fresh','alive') AND COALESCE(migrated_at, created_at) < ?)
               OR (state='fresh' AND checks=0 AND created_at < ?)''',
                              (now - reservoir_minutes * 60, now - 600))
        self.db.commit()
        return cur.rowcount

    # live flow -----------------------------------------------------------
    def seen(self, mint: str, sig: str) -> bool:
        return self.db.execute('SELECT 1 FROM processed WHERE mint=? AND signature=?',
                               (mint, sig)).fetchone() is not None

    def mark(self, mint: str, sig: str, now: float | None = None):
        self.db.execute('INSERT OR IGNORE INTO processed VALUES(?,?,?)', (sig, mint, now or time.time()))
        self.db.commit()

    def add_trades(self, mint: str, sig: str, trades: list[Trade]) -> int:
        if not trades or not self.db.execute("SELECT 1 FROM tokens WHERE mint=? AND state='watching'",
                                             (mint,)).fetchone():
            return 0
        added = 0
        for t in trades:
            cur = self.db.execute('INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                  (sig, t.idx, mint, t.at, t.slot, t.wallet, t.side, t.tokens,
                                   t.sol, t.price, t.src))
            added += cur.rowcount
        latest = max(trades, key=lambda t: (t.at, t.idx))
        priced = [t for t in sorted(trades, key=lambda t: (t.at, t.idx)) if t.price]
        self.db.execute('''UPDATE tokens SET last_trade_at=MAX(COALESCE(last_trade_at, 0), ?),
            last_sig=?, first_price=COALESCE(first_price, ?), last_price=COALESCE(?, last_price),
            progress=COALESCE(?, progress) WHERE mint=?''',
                        (latest.at, sig, priced[0].price if priced else None,
                         priced[-1].price if priced else None,
                         latest.progress * 100 if latest.progress is not None else None, mint))
        self.db.commit()
        return added

    def trades(self, mint: str, since: float = 0) -> list[Trade]:
        rows = self.db.execute('''SELECT * FROM trades WHERE mint=? AND at>=?
            ORDER BY at, slot, idx''', (mint, since)).fetchall()
        return [Trade(r['wallet'], r['side'], r['tokens'], r['sol'], r['at'], r['price'], None,
                      r['slot'], r['idx'], r['src']) for r in rows]

    # calls ---------------------------------------------------------------
    def has_call(self, mint: str) -> bool:
        return self.db.execute('SELECT 1 FROM calls WHERE mint=?', (mint,)).fetchone() is not None

    def claim_call(self, mint: str, stage: str, cap: int, now: float) -> bool:
        """Reserve the one call a mint may get, before anything is sent."""
        if self.db.execute('SELECT COUNT(*) FROM calls WHERE at>?', (now - 3600,)).fetchone()[0] >= cap:
            return False
        cur = self.db.execute("INSERT OR IGNORE INTO calls(mint, at, state, stage) VALUES(?,?,'attempting',?)",
                              (mint, now, stage))
        self.db.commit()
        return cur.rowcount == 1

    def finish_call(self, mint: str, state: str, message_id: int | None, details: dict):
        allowed = {'symbol', 'name', 'score', 'grade', 'price_sol', 'mcap_sol', 'sol_usd', 'progress',
                   'net_sol', 'buyers', 'top10', 'dev_pct', 'launches', 'socials', 'note'}
        details = {k: v for k, v in details.items() if k in allowed}
        assignments = ''.join(f', {k}=?' for k in details)
        self.db.execute(f'UPDATE calls SET state=?, message_id=?{assignments} WHERE mint=?',
                        (state, message_id, *details.values(), mint))
        self.db.execute("UPDATE tokens SET state='called' WHERE mint=?", (mint,))
        self.db.commit()

    def call(self, mint: str) -> dict | None:
        row = self.db.execute('SELECT * FROM calls WHERE mint=?', (mint,)).fetchone()
        return dict(row) if row else None

    # outcomes ------------------------------------------------------------
    def open_outcome(self, mint: str, entry: float, now: float):
        self.db.execute('''INSERT OR IGNORE INTO outcomes(mint, call_at, entry, last, last_at, peak,
            peak_at, low) VALUES(?,?,?,?,?,?,?,?)''', (mint, now, entry, entry, now, entry, now, entry))
        self.db.commit()

    def track(self, mint: str, price: float, at: float):
        """A price after the call moves last, peak and low (earlier prices are ignored)."""
        self.db.execute('''UPDATE outcomes SET last=CASE WHEN ? >= last_at THEN ? ELSE last END,
            last_at=MAX(last_at, ?), peak=MAX(peak, ?),
            peak_at=CASE WHEN ? > peak THEN ? ELSE peak_at END, low=MIN(low, ?)
            WHERE mint=? AND done=0 AND ? >= call_at''',
                        (at, price, at, price, price, at, price, mint, at))
        self.db.commit()

    def outcome(self, mint: str) -> dict | None:
        row = self.db.execute('SELECT * FROM outcomes WHERE mint=?', (mint,)).fetchone()
        return dict(row) if row else None

    def open_outcomes(self) -> list[dict]:
        return self._rows('''SELECT o.*, c.message_id, c.symbol, c.name, c.stage, t.creator, t.account,
            t.stage AS token_stage FROM outcomes o JOIN calls c USING(mint) LEFT JOIN tokens t USING(mint)
            WHERE o.done=0''')

    def set_outcome(self, mint: str, **fields):
        fields = {k: v for k, v in fields.items() if k in self.outcome_columns and k != 'mint'}
        if fields:
            assignments = ', '.join(f'{k}=?' for k in fields)
            self.db.execute(f'UPDATE outcomes SET {assignments} WHERE mint=?', (*fields.values(), mint))
            self.db.commit()

    def calls_since(self, since: float) -> list[dict]:
        return self._rows('''SELECT c.*, o.entry, o.last, o.peak, o.low, o.r5, o.r15, o.r30, o.r60,
            o.dev_sold, o.gap, o.done FROM calls c LEFT JOIN outcomes o USING(mint)
            WHERE c.at>=? ORDER BY c.at''', (since,))

    def missed(self, since: float, ratio: float) -> list[dict]:
        """Tokens whose swept market cap later reached `ratio` x its first reading without a call."""
        return self._rows('''SELECT mint, symbol, first_mcap_sol, peak_mcap_sol, reason, state FROM tokens
            WHERE created_at>=? AND first_mcap_sol>0 AND peak_mcap_sol>=first_mcap_sol*?
              AND mint NOT IN (SELECT mint FROM calls)
            ORDER BY peak_mcap_sol/first_mcap_sol DESC LIMIT 15''', (since, ratio))

    # budget and upkeep ----------------------------------------------------
    def budget_claim(self, kind: str):
        """Count one RPC request of `kind` today (UTC). The ceiling is decided by rpc.allowed."""
        day = time.strftime('%Y-%m-%d', time.gmtime())
        self.db.execute('INSERT INTO spend(day, kind, used) VALUES(?, ?, 1) '
                        'ON CONFLICT(day, kind) DO UPDATE SET used = used + 1', (day, kind))
        self.db.commit()

    def budget_by_kind(self) -> dict:
        day = time.strftime('%Y-%m-%d', time.gmtime())
        return {r['kind']: r['used'] for r in self.db.execute('SELECT kind, used FROM spend WHERE day=?', (day,))}

    def budget_used(self) -> int:
        return sum(self.budget_by_kind().values())

    def cleanup(self, now: float):
        self.db.execute('DELETE FROM trades WHERE at<?', (now - 2 * 86400,))
        self.db.execute('DELETE FROM processed WHERE at<?', (now - 86400,))
        self.db.execute("DELETE FROM tokens WHERE state IN ('dead','expired') AND created_at<?",
                        (now - 3 * 86400,))
        self.db.execute('DELETE FROM creators WHERE last_seen<?', (now - 7 * 86400,))
        self.db.execute('DELETE FROM spend WHERE day<?',
                        (time.strftime('%Y-%m-%d', time.gmtime(now - 7 * 86400)),))
        self.db.commit()

    def stats(self) -> dict:
        return {'tokens': {r[0]: r[1] for r in self.db.execute(
                    'SELECT state, COUNT(*) FROM tokens GROUP BY state')},
                'calls': {r[0]: r[1] for r in self.db.execute(
                    'SELECT state, COUNT(*) FROM calls GROUP BY state')},
                'metrics': self.metrics(),
                'rpc_today': self.budget_used(),
                'rpc_today_by_kind': self.budget_by_kind()}
