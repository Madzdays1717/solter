"""SQLite persistence for token state and notification deduplication."""

import json
import sqlite3
import time
from pathlib import Path

from .addresses import valid_address
from .momentum import Token
from .outcomes import Outcome


class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=8)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=NORMAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS launches (
                mint TEXT PRIMARY KEY, created_at REAL NOT NULL,
                name TEXT NOT NULL, symbol TEXT NOT NULL,
                phase TEXT NOT NULL, restricted INTEGER NOT NULL DEFAULT 0,
                pool TEXT NOT NULL DEFAULT '', image TEXT NOT NULL DEFAULT '',
                last_reason TEXT NOT NULL DEFAULT 'awaiting activity',
                last_seen REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                mint TEXT PRIMARY KEY, decided_at REAL NOT NULL,
                state TEXT NOT NULL, message_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS stream_state (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decision_outcomes (
                mint TEXT PRIMARY KEY,
                observed_at REAL NOT NULL,
                first_price_sol REAL NOT NULL,
                last_price_sol REAL NOT NULL,
                max_price_sol REAL NOT NULL,
                min_price_sol REAL NOT NULL,
                last_at REAL NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                observed_trades INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decision_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mint TEXT NOT NULL,
                at REAL NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                price_sol REAL NOT NULL,
                buy_sol REAL NOT NULL,
                sell_sol REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_decision_history_mint ON decision_history(mint, at);
            CREATE TABLE IF NOT EXISTS social_observations (
                mint TEXT PRIMARY KEY, observed_at REAL NOT NULL,
                status TEXT NOT NULL, posts INTEGER NOT NULL,
                authors INTEGER NOT NULL, recent INTEGER NOT NULL,
                earlier INTEGER NOT NULL, similar_pct REAL NOT NULL,
                topic TEXT NOT NULL
            );
        ''')
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(launches)')}
        extra_columns = {
            'creator': "TEXT NOT NULL DEFAULT ''",
            'initial_buy_sol': 'REAL NOT NULL DEFAULT 0',
            'supply': 'REAL NOT NULL DEFAULT 0',
            'creator_buy_sol': 'REAL NOT NULL DEFAULT 0',
            'creator_sell_sol': 'REAL NOT NULL DEFAULT 0',
            'authority_observed': 'INTEGER NOT NULL DEFAULT 0',
            'early_buyers': "TEXT NOT NULL DEFAULT '[]'",
            'banner': "TEXT NOT NULL DEFAULT ''",
            'twitter': "TEXT NOT NULL DEFAULT ''",
            'telegram': "TEXT NOT NULL DEFAULT ''",
            'website': "TEXT NOT NULL DEFAULT ''",
            'reported_mc_usd': 'REAL NOT NULL DEFAULT 0',
            'reported_mc_at': 'REAL NOT NULL DEFAULT 0',
        }
        for column, definition in extra_columns.items():
            if column not in columns:
                self.db.execute(f'ALTER TABLE launches ADD COLUMN {column} {definition}')
        self.db.commit()
        self._tables = {row[0] for row in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    def close(self):
        self.db.close()

    def load_launches(self) -> dict[str, Token]:
        rows = self.db.execute('SELECT * FROM launches WHERE created_at >= ?',
                               (time.time() - 5500,))
        result: dict[str, Token] = {}
        for row in rows:
            try:
                earlier = json.loads(row['early_buyers'])
                early = {addr for addr in earlier if valid_address(addr)}
            except (TypeError, ValueError):
                early = set()
            token = Token(
                mint=row['mint'], launched_at=row['created_at'],
                name=row['name'], symbol=row['symbol'], phase=row['phase'],
                restricted=bool(row['restricted']), pool=row['pool'], image=row['image'], banner=row['banner'],
                creator=row['creator'], initial_buy_sol=row['initial_buy_sol'],
                supply=row['supply'],
                authority_observed=bool(row['authority_observed']),
                creator_buy_sol=row['creator_buy_sol'], creator_sell_sol=row['creator_sell_sol'],
                early_buyers=early, twitter=row['twitter'], telegram=row['telegram'],
                website=row['website'], reported_mc_usd=row['reported_mc_usd'],
                reported_mc_at=row['reported_mc_at'],
            )
            result[token.mint] = token
        return result

    def save_launches(self, tokens: list[Token]):
        with self.db:
            self.db.executemany('''
                INSERT INTO launches (
                    mint,created_at,name,symbol,phase,restricted,pool,image,last_seen,
                    creator,initial_buy_sol,creator_buy_sol,creator_sell_sol,
                    authority_observed,early_buyers,supply,twitter,telegram,website,
                    reported_mc_usd,reported_mc_at,banner
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(mint) DO UPDATE SET
                    name=excluded.name,symbol=excluded.symbol,phase=excluded.phase,
                    restricted=excluded.restricted,pool=excluded.pool,
                    image=excluded.image,banner=excluded.banner,last_seen=excluded.last_seen,
                    creator=excluded.creator,initial_buy_sol=excluded.initial_buy_sol,
                    creator_buy_sol=excluded.creator_buy_sol,
                    creator_sell_sol=excluded.creator_sell_sol,
                    authority_observed=excluded.authority_observed,
                    early_buyers=excluded.early_buyers,
                    supply=excluded.supply,
                    twitter=excluded.twitter,telegram=excluded.telegram,
                    website=excluded.website,reported_mc_usd=excluded.reported_mc_usd,
                    reported_mc_at=excluded.reported_mc_at
            ''', [
                (token.mint, token.launched_at, token.name, token.symbol, token.phase,
                 int(token.restricted), token.pool, token.image,
                 token.last_event_at or token.launched_at,
                 token.creator, token.initial_buy_sol,
                 token.creator_buy_sol, token.creator_sell_sol,
                 int(token.authority_observed),
                 json.dumps(sorted(token.early_buyers)), token.supply,
                 token.twitter, token.telegram, token.website,
                 token.reported_mc_usd, token.reported_mc_at, token.banner)
                for token in tokens
            ])

    def mark_reasons(self, reasons: dict[str, str]):
        if not reasons:
            return
        with self.db:
            self.db.executemany('UPDATE launches SET last_reason=? WHERE mint=?',
                                [(reason, mint) for mint, reason in reasons.items()])

    def save_social(self, mint: str, report) -> None:
        with self.db:
            self.db.execute('''
                INSERT INTO social_observations
                (mint, observed_at, status, posts, authors, recent, earlier, similar_pct, topic)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(mint) DO UPDATE SET
                    observed_at=excluded.observed_at,status=excluded.status,
                    posts=excluded.posts,authors=excluded.authors,
                    recent=excluded.recent,earlier=excluded.earlier,
                    similar_pct=excluded.similar_pct,topic=excluded.topic
            ''', (mint, report.observed_at, report.status,
                  report.posts, report.authors, report.recent, report.earlier,
                  report.similar_pct, report.topic))

    def load_outcomes(self) -> dict[str, Outcome]:
        # Resume passive observations after a restart.
        rows = self.db.execute('SELECT * FROM decision_outcomes WHERE observed_at >= ?',
                               (time.time() - 1900,))
        return {row['mint']: Outcome(
            mint=row['mint'], observed_at=row['observed_at'],
            first_price_sol=row['first_price_sol'], last_price_sol=row['last_price_sol'],
            max_price_sol=row['max_price_sol'], min_price_sol=row['min_price_sol'],
            last_at=row['last_at'], status=row['status'], reason=row['reason'],
            observed_trades=row['observed_trades'], dirty=False,
        ) for row in rows}

    def save_outcomes(self, states: list[Outcome], history: list[tuple]):
        with self.db:
            if states:
                self.db.executemany('''
                    INSERT INTO decision_outcomes
                    (mint,observed_at,first_price_sol,last_price_sol,max_price_sol,
                     min_price_sol,last_at,status,reason,observed_trades)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(mint) DO UPDATE SET
                    last_price_sol=excluded.last_price_sol,
                    max_price_sol=excluded.max_price_sol,
                    min_price_sol=excluded.min_price_sol,
                    last_at=excluded.last_at,status=excluded.status,
                    reason=excluded.reason,observed_trades=excluded.observed_trades
                ''', [(x.mint, x.observed_at, x.first_price_sol, x.last_price_sol,
                       x.max_price_sol, x.min_price_sol, x.last_at, x.status,
                       x.reason, x.observed_trades) for x in states])
            if history:
                self.db.executemany('''INSERT INTO decision_history
                    (mint,at,status,reason,price_sol,buy_sol,sell_sol)
                    VALUES (?,?,?,?,?,?,?)''', history)

    def prune_history(self):
        """Keep durable outcome evidence without indefinite disk growth."""
        cutoff = time.time() - 30 * 86400
        with self.db:
            self.db.execute('DELETE FROM decision_history WHERE at < ?', (cutoff,))
            self.db.execute('DELETE FROM decision_outcomes WHERE observed_at < ?', (cutoff,))

    def already_alerted(self, mint: str) -> bool:
        for table in ('deliveries', 'alerts', 'calls'):
            if table not in self._tables:
                continue
            if self.db.execute(f'SELECT 1 FROM {table} WHERE mint=? LIMIT 1',
                               (mint,)).fetchone():
                return True
        return False

    def reserve(self, mint: str) -> bool:
        if self.already_alerted(mint):
            return False
        with self.db:
            cursor = self.db.execute('''INSERT OR IGNORE INTO deliveries
                (mint, decided_at, state) VALUES(?, ?, 'pending')''', (mint, time.time()))
        return cursor.rowcount == 1

    def finish(self, mint: str, state: str, message_id: int | None):
        with self.db:
            self.db.execute('UPDATE deliveries SET state=?, message_id=? WHERE mint=?',
                            (state, message_id, mint))

    def checkpoint(self) -> tuple[int, float]:
        rows = dict(self.db.execute('SELECT key,value FROM stream_state').fetchall())
        return int(rows.get('slot', 0)), float(rows.get('at', 0))

    def save_checkpoint(self, slot: int, now: float):
        with self.db:
            self.db.executemany('''INSERT INTO stream_state(key,value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value''',
                [('slot', str(slot)), ('at', str(now))])

    def stats(self) -> dict:
        return {
            'launches': self.db.execute('SELECT COUNT(*) FROM launches').fetchone()[0],
            'active_launches': self.db.execute('SELECT COUNT(*) FROM launches WHERE created_at > ?',
                                              (time.time() - 3600,)).fetchone()[0],
            'alerts': {row['state']: row['total'] for row in self.db.execute(
                'SELECT state, COUNT(*) total FROM deliveries GROUP BY state')},
            'checkpoint_slot': self.checkpoint()[0],
            'social_tokens': self.db.execute('SELECT COUNT(*) FROM social_observations').fetchone()[0],
            'reviewed_tokens': self.db.execute('SELECT COUNT(*) FROM decision_outcomes').fetchone()[0],
            'decision_samples': self.db.execute('SELECT COUNT(*) FROM decision_history').fetchone()[0],
            'observed_2x': self.db.execute('''SELECT COUNT(*) FROM decision_outcomes
                WHERE first_price_sol > 0 AND max_price_sol >= 2 * first_price_sol''').fetchone()[0],
            'outcomes_with_10_trades': self.db.execute('''SELECT COUNT(*) FROM decision_outcomes
                WHERE observed_trades >= 10''').fetchone()[0],
        }

    def trace(self, mint: str) -> dict:
        if not valid_address(mint):
            return {'error': 'invalid mint'}
        launch = self.db.execute('SELECT * FROM launches WHERE mint=?', (mint,)).fetchone()
        delivery = self.db.execute('SELECT * FROM deliveries WHERE mint=?', (mint,)).fetchone()
        if launch:
            launch = dict(launch)
            launch['early_buyers'] = len(json.loads(launch['early_buyers']))
        social = self.db.execute('SELECT * FROM social_observations WHERE mint=?',
                                 (mint,)).fetchone()
        outcome = self.db.execute('SELECT * FROM decision_outcomes WHERE mint=?',
                                  (mint,)).fetchone()
        history = self.db.execute('''SELECT at,status,reason,price_sol,buy_sol,sell_sol
            FROM decision_history WHERE mint=? ORDER BY at DESC LIMIT 12''', (mint,))
        outcome_info = dict(outcome) if outcome else None
        if outcome_info and outcome_info['first_price_sol'] > 0:
            first = outcome_info['first_price_sol']
            outcome_info['observed_max_change_pct'] = round(
                (outcome_info['max_price_sol'] / first - 1) * 100, 2)
            outcome_info['observed_min_change_pct'] = round(
                (outcome_info['min_price_sol'] / first - 1) * 100, 2)
        return {'launch': launch,
                'delivery': dict(delivery) if delivery else None,
                'social': dict(social) if social else None,
                'outcome': outcome_info,
                'decision_history': [dict(row) for row in history]}
