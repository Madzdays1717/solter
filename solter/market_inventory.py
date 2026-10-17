"""Persistent, fair, free-market candidate scheduler.

Nothing is discarded just because the current API batch is full.  The feed
adds candidates; one bounded worker schedules refreshes.  Neither a positive
signal nor absence of a DEX index is evidence of on-chain safety.
"""
from __future__ import annotations

import time


class MarketInventory:
    def __init__(self, store):
        self.db = store.db
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS market_queue (
            mint TEXT PRIMARY KEY, first_seen REAL NOT NULL,
            due_at REAL NOT NULL, last_checked REAL,
            source TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            first_mcap_usd REAL, first_market_at REAL,
            last_reason TEXT NOT NULL DEFAULT '', last_delay REAL);
        CREATE INDEX IF NOT EXISTS market_queue_due ON market_queue(due_at, priority);
        ''')

    def add(self, mints: list[str], source: str, now: float | None = None, priority: int = 0):
        from .chain import valid_address
        now = time.time() if now is None else now
        items = [(m, now, now, source[:30], max(0, min(3, priority))) for m in dict.fromkeys(mints)
                 if valid_address(m)]
        if not items:
            return 0
        self.db.executemany('''INSERT INTO market_queue(mint,first_seen,due_at,source,priority)
            VALUES(?,?,?,?,?) ON CONFLICT(mint) DO UPDATE SET
            priority=MAX(priority,excluded.priority),
            due_at=CASE WHEN excluded.priority > priority THEN
                MIN(due_at,excluded.due_at) ELSE due_at END''', items)
        self.db.commit()
        return len(items)

    def seed_existing(self, now: float | None = None):
        """Recover candidate pool from older versions with no queue/history."""
        now = time.time() if now is None else now
        self.db.execute('''INSERT OR IGNORE INTO market_queue(mint,first_seen,due_at,source,priority)
            SELECT mint, ?, ?, 'previous_version', 0
              FROM tokens WHERE stage='migrated'
              AND mint NOT IN (SELECT mint FROM calls)''', (now, now))
        self.db.commit()

    def due(self, now: float, limit: int = 30) -> list[str]:
        """Reserve capacity for cold revival: hot arrivals never starve old tokens."""
        limit = max(1, min(30, limit))
        hot_size = max(1, round(limit * .6))
        cold_size = limit - hot_size
        def select(sql, count):
            if count <= 0:
                return []
            return [r[0] for r in self.db.execute(sql, (now, count))]
        tail = ''' AND mint NOT IN (SELECT mint FROM calls)
                  ORDER BY due_at ASC, first_seen ASC LIMIT ?'''
        hot = select('SELECT mint FROM market_queue WHERE due_at<=? AND priority>=2' + tail, hot_size)
        cold = select('SELECT mint FROM market_queue WHERE due_at<=? AND priority<2' + tail, cold_size)
        selected = list(dict.fromkeys(hot + cold))
        if len(selected) < limit:
            additional = select('''SELECT mint FROM market_queue WHERE due_at<=?
                AND mint NOT IN (SELECT mint FROM calls)
                ORDER BY due_at ASC, first_seen ASC LIMIT ?''', limit)
            selected.extend(m for m in additional if m not in selected)
        selected = selected[:limit]
        # Prevent re-selection even when a network request fails midway.
        self.db.executemany('''UPDATE market_queue SET due_at=?, last_checked=?,
                attempts=attempts+1 WHERE mint=?''', [(now + 30, now, m) for m in selected])
        self.db.commit()
        return selected

    def result(self, mint: str, now: float, *, indexed: bool, token: dict | None = None,
               failed: bool = False, reason_override: str | None = None,
               hot_refresh: float = 15):
        row = self.db.execute('SELECT * FROM market_queue WHERE mint=?', (mint,)).fetchone()
        if row is None:
            return
        attempts = row['attempts']
        if failed:
            delay, priority, reason = 20, row['priority'], 'provider unavailable'
        elif not indexed:
            # Recently seen migrations get more retries; then move to cold rotation.
            delay = 20 if now - row['first_seen'] < 180 else min(300, 45 + attempts * 5)
            priority, reason = (2 if delay == 20 else 0), 'DEX not indexed yet'
        else:
            buys = token.get('market_buys') or 0
            sells = token.get('market_sells') or 0
            change = token.get('market_change_pct') or 0
            moving = buys >= 8 and buys >= 1.2 * max(1, sells) and change >= 1
            delay = max(5, min(60, hot_refresh)) if moving else 75
            priority = 2 if moving else 0
            reason = token.get('reason') or ('active market' if moving else 'quiet market')
        if reason_override and not failed and not indexed:
            reason = reason_override
            delay, priority = 90, 0
        mcap = token.get('market_mcap_usd') if indexed and token else None
        self.db.execute('''UPDATE market_queue SET due_at=?, priority=?, last_reason=?,
            first_mcap_usd=COALESCE(first_mcap_usd,?),
            first_market_at=CASE WHEN first_mcap_usd IS NULL AND ? IS NOT NULL
                THEN ? ELSE first_market_at END,
            last_delay=? WHERE mint=?''',
            (now + delay, priority, reason, mcap, mcap, now, delay, mint))
        self.db.commit()

    def record_reason(self, mint: str, reason: str):
        self.db.execute('UPDATE market_queue SET last_reason=? WHERE mint=?',
                        (reason[:120], mint))
        self.db.commit()

    def trace(self, mint: str) -> dict | None:
        row = self.db.execute('SELECT * FROM market_queue WHERE mint=?', (mint,)).fetchone()
        return dict(row) if row else None

    def prune(self, now: float):
        # An active token is never removed merely for being old. Only old
        # unindexed/non-moving candidates are pruned; feeds can rediscover them.
        cur = self.db.execute('''DELETE FROM market_queue WHERE priority=0
            AND last_checked<? AND first_seen<?
            AND mint NOT IN (SELECT mint FROM calls)''', (now - 7 * 86400, now - 7 * 86400))
        self.db.commit()
        return cur.rowcount
