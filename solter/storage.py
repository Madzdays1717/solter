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
  reason TEXT NOT NULL DEFAULT '',
  market_at REAL, market_buys INTEGER, market_sells INTEGER,
  market_volume_usd REAL, market_liquidity_usd REAL, market_change_pct REAL,
  market_mcap_usd REAL, pair_created_at REAL);
CREATE INDEX IF NOT EXISTS tokens_state ON tokens(state, created_at);
CREATE INDEX IF NOT EXISTS tokens_creator ON tokens(creator);
CREATE INDEX IF NOT EXISTS tokens_sweep ON tokens(state, stage, checks, created_at);
CREATE INDEX IF NOT EXISTS tokens_recheck ON tokens(state, stage, checked_at);
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
CREATE TABLE IF NOT EXISTS metrics (key TEXT PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0);
'''

HORIZONS = (5, 15, 30, 60)   # minutes after a call at which its return is recorded


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        # Speed up bursts of short transactions on Termux; WAL remains transactional.
        self.db.execute('PRAGMA synchronous=NORMAL')
        self.db.execute('PRAGMA busy_timeout=10000')
        self.db.executescript(SCHEMA)
        # Existing SQLite installations retain all calls and history.
        installed = {r['name'] for r in self.db.execute('PRAGMA table_info(tokens)')}
        for name, typ in (('market_at', 'REAL'), ('market_buys', 'INTEGER'),
                          ('market_sells', 'INTEGER'), ('market_volume_usd', 'REAL'),
                          ('market_liquidity_usd', 'REAL'), ('market_change_pct', 'REAL'),
                          ('market_mcap_usd', 'REAL'), ('pair_created_at', 'REAL')):
            if name not in installed:
                self.db.execute(f'ALTER TABLE tokens ADD COLUMN {name} {typ}')
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

    def update_many(self, changes: list[tuple[str, dict]]):
        """Commit a scan batch once instead of fsyncing for every token."""
        with self.db:
            for mint, fields in changes:
                fields = {k: v for k, v in fields.items() if k in self.columns and k != 'mint'}
                if fields:
                    assignments = ', '.join(f'{k}=?' for k in fields)
                    self.db.execute(f'UPDATE tokens SET {assignments} WHERE mint=?',
                                    (*fields.values(), mint))

    def metric_many(self, changes: dict[str, int]):
        with self.db:
            for key, n in changes.items():
                self.db.execute('INSERT INTO metrics(key, n) VALUES(?, ?) '
                                'ON CONFLICT(key) DO UPDATE SET n = n + excluded.n',
                                (key[:80], n))

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
        """Fair selection: newest launches plus oldest still-unchecked, then returning demand.

        An always-newest queue silently starves older unexamined launches when
        creation rates exceed RPC scan throughput. Never spend >limit reads.
        """
        limit = max(1, limit)
        latest = self._rows('''SELECT * FROM tokens WHERE state='fresh' AND checks=0
            AND stage='new' AND created_at<=? ORDER BY created_at DESC LIMIT ?''',
            (now - first_check, max(1, int(limit * .45))))
        oldest = self._rows('''SELECT * FROM tokens WHERE state='fresh' AND checks=0
            AND stage='new' AND created_at<=? ORDER BY created_at ASC LIMIT ?''',
            (now - first_check, max(1, int(limit * .35))))
        alive = self._rows('''SELECT * FROM tokens WHERE state='alive' AND stage='new'
            AND (checked_at IS NULL OR checked_at<=?)
            ORDER BY traction DESC, checked_at ASC LIMIT ?''',
            (now - recheck, max(1, int(limit * .15))))
        quiet = self._rows('''SELECT * FROM tokens WHERE state='fresh' AND checks>0
            AND stage='new' AND checked_at<=? ORDER BY checked_at ASC LIMIT ?''',
            (now - recheck, max(1, int(limit * .20))))
        batch, seen = [], set()
        for row in latest + oldest + alive + quiet:
            if row['mint'] not in seen and row['account'] and len(batch) < limit:
                batch.append(row)
                seen.add(row['mint'])
        if len(batch) < limit:
            remainder = self._rows('''SELECT * FROM tokens WHERE stage='new' AND
                ((state='fresh' AND checks=0 AND created_at<=?) OR
                 (state='fresh' AND checks>0 AND checked_at<=?) OR
                 (state='alive' AND (checked_at IS NULL OR checked_at<=?)))
                ORDER BY CASE WHEN checks=0 THEN 0 ELSE 1 END, created_at ASC LIMIT ?''',
                (now - first_check, now - recheck, now - recheck, limit + len(seen)))
            for row in remainder:
                if row['mint'] not in seen and row['account'] and len(batch) < limit:
                    batch.append(row)
                    seen.add(row['mint'])
        return batch

    def market_candidates(self, limit: int = 90) -> list[str]:
        """Fair rotation of migrated inventory, including old tokens that may reawaken."""
        return [r['mint'] for r in self._rows('''SELECT mint FROM tokens
            WHERE stage='migrated' AND state IN ('alive','watching','fresh','expired')
              AND mint NOT IN (SELECT mint FROM calls)
            ORDER BY COALESCE(market_at,0) ASC LIMIT ?''', (limit,))]

    def update_market(self, mint: str, pool: str, data: dict, now: float,
                      eligible: bool | None = None) -> bool:
        """Only verified caller-supplied PumpSwap pairs; never overwrite a sent call."""
        token = self.token(mint)
        if token and self.has_call(mint):
            return False
        if token is None:
            self.db.execute('''INSERT INTO tokens(mint, symbol, name, created_at, stage, state,
                account, migrated_at) VALUES (?, ?, ?, ?, 'migrated', 'alive', ?, ?)''',
                (mint, data['symbol'], data['name'], data['pair_created_at'], pool,
                 data['pair_created_at']))
        elif token['stage'] != 'migrated':
            self.migrate(mint, pool, now)
        else:
            self.db.execute("UPDATE tokens SET account=? WHERE mint=?", (pool, mint))
        updates = {k: v for k, v in data.items() if k in self.columns and k != 'mint'}
        if token and token['symbol']:
            updates.pop('symbol', None)
        if token and token['name']:
            updates.pop('name', None)
        # Do not spend watch slots on a stale/non-performing DEX listing.
        from .market import market_ready
        if eligible is None:
            eligible = market_ready(data, now, 7, 650, 5000)
        if token and token['state'] in ('expired','dead','fresh') and eligible:
            updates.update(state='alive', reason='market activity revived')
        values = ', '.join(f'{k}=?' for k in updates)
        self.db.execute(f'UPDATE tokens SET {values} WHERE mint=?', (*updates.values(), mint))
        self.db.commit()
        return token is None

    def alive(self) -> list[dict]:
        return self._rows("""SELECT * FROM tokens WHERE state='alive'
            ORDER BY CASE WHEN stage='migrated' THEN 0 ELSE 1 END,
                     COALESCE(market_at,checked_at,created_at) DESC LIMIT 1200""")

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
            WHERE (state IN ('fresh','alive') AND
                (CASE WHEN stage='migrated' THEN COALESCE(market_at,migrated_at,created_at)
                 ELSE created_at END) < ?)
               OR (state='fresh' AND stage='new' AND checks=0 AND created_at < ?)''',
                              (now - reservoir_minutes * 60, now - max(600, reservoir_minutes * 60)))
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
