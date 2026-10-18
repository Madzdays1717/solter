"""SQLite persistence for token state and notification deduplication."""

import json
import sqlite3
import time
from pathlib import Path

from .addresses import valid_address
from .momentum import Token


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
                               (time.time() - 3600,))
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
                restricted=bool(row['restricted']), pool=row['pool'], image=row['image'],
                creator=row['creator'], initial_buy_sol=row['initial_buy_sol'],
                supply=row['supply'],
                authority_observed=bool(row['authority_observed']),
                creator_buy_sol=row['creator_buy_sol'], creator_sell_sol=row['creator_sell_sol'],
                early_buyers=early,
            )
            result[token.mint] = token
        return result

    def save_launches(self, tokens: list[Token]):
        with self.db:
            self.db.executemany('''
                INSERT INTO launches (
                    mint,created_at,name,symbol,phase,restricted,pool,image,last_seen,
                    creator,initial_buy_sol,creator_buy_sol,creator_sell_sol,
                    authority_observed,early_buyers,supply
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(mint) DO UPDATE SET
                    name=excluded.name,symbol=excluded.symbol,phase=excluded.phase,
                    restricted=excluded.restricted,pool=excluded.pool,
                    image=excluded.image,last_seen=excluded.last_seen,
                    creator=excluded.creator,initial_buy_sol=excluded.initial_buy_sol,
                    creator_buy_sol=excluded.creator_buy_sol,
                    creator_sell_sol=excluded.creator_sell_sol,
                    authority_observed=excluded.authority_observed,
                    early_buyers=excluded.early_buyers,
                    supply=excluded.supply
            ''', [
                (token.mint, token.launched_at, token.name, token.symbol, token.phase,
                 int(token.restricted), token.pool, token.image,
                 token.last_event_at or token.launched_at,
                 token.creator, token.initial_buy_sol,
                 token.creator_buy_sol, token.creator_sell_sol,
                 int(token.authority_observed),
                 json.dumps(sorted(token.early_buyers)), token.supply)
                for token in tokens
            ])

    def mark_reasons(self, reasons: dict[str, str]):
        if not reasons:
            return
        with self.db:
            self.db.executemany('UPDATE launches SET last_reason=? WHERE mint=?',
                                [(reason, mint) for mint, reason in reasons.items()])

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
        }

    def trace(self, mint: str) -> dict:
        if not valid_address(mint):
            return {'error': 'invalid mint'}
        launch = self.db.execute('SELECT * FROM launches WHERE mint=?', (mint,)).fetchone()
        delivery = self.db.execute('SELECT * FROM deliveries WHERE mint=?', (mint,)).fetchone()
        if launch:
            launch = dict(launch)
            launch['early_buyers'] = len(json.loads(launch['early_buyers']))
        return {'launch': launch,
                'delivery': dict(delivery) if delivery else None}
