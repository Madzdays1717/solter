"""Persistent inventory, alert history, and explanations in one small SQLite file.

No wallet tracking, blockchain transaction cache or milestone tables. Legacy
versions' tables are left untouched, so backups and historic call records remain.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import time

from .addresses import valid_mint
from .signals import Decision, MarketSnapshot


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA busy_timeout=10000')
        self.connection.executescript('''
            CREATE TABLE IF NOT EXISTS candidates (
              mint TEXT PRIMARY KEY, first_seen REAL NOT NULL,
              last_seen REAL NOT NULL, source TEXT NOT NULL,
              last_source TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 1,
              next_scan REAL NOT NULL, last_scan REAL,
              scans INTEGER NOT NULL DEFAULT 0,
              first_market_at REAL, first_market_cap REAL,
              last_market_at REAL, last_market_cap REAL,
              last_volume_5m REAL, last_change_5m REAL,
              last_reason TEXT NOT NULL DEFAULT 'awaiting market snapshot',
              symbol TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '',
              image_url TEXT
            );
            CREATE INDEX IF NOT EXISTS candidates_due
              ON candidates(next_scan, priority);
            CREATE TABLE IF NOT EXISTS alerts (
              mint TEXT PRIMARY KEY, decided_at REAL NOT NULL,
              state TEXT NOT NULL, message_id INTEGER,
              reason TEXT NOT NULL, score INTEGER NOT NULL,
              sent_at REAL, note TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS alerts_time ON alerts(decided_at);
            CREATE TABLE IF NOT EXISTS counters (
              name TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0
            );
        ''')
        # Carry forward pre-rebuild calls without re-alerting them. The old
        # database remains intact; no destructive schema migration.
        tables = {row['name'] for row in self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if 'calls' in tables:
            self.connection.execute('''INSERT OR IGNORE INTO alerts
                (mint,decided_at,state,reason,score,note)
                SELECT mint, at, 'legacy', 'sent by previous Solter',
                  COALESCE(score,0), 'imported from calls' FROM calls''')
        self.connection.commit()

    def close(self):
        self.connection.close()

    def increment(self, name: str, value: int = 1):
        self.connection.execute('''INSERT INTO counters(name,value) VALUES(?,?)
            ON CONFLICT(name) DO UPDATE SET value=value+excluded.value''',
            (name, value))
        self.connection.commit()

    def ingest(self, mints: list[str], source: str, priority: int = 1,
               media: dict[str, str | None] | None = None) -> int:
        now = time.time()
        count = 0
        priority = min(3, max(0, priority))
        with self.connection:
            for mint in dict.fromkeys(mints):
                if not valid_mint(mint):
                    continue
                image = (media or {}).get(mint)
                self.connection.execute('''INSERT INTO candidates (
                    mint,first_seen,last_seen,source,last_source,priority,next_scan,image_url)
                    VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(mint) DO UPDATE SET
                      last_seen=excluded.last_seen,
                      last_source=excluded.last_source,
                      priority=MAX(candidates.priority,excluded.priority),
                      next_scan=CASE WHEN excluded.priority>candidates.priority
                        THEN MIN(candidates.next_scan,excluded.next_scan)
                        ELSE candidates.next_scan END,
                      image_url=COALESCE(excluded.image_url,candidates.image_url)
                ''', (mint, now, now, source, source, priority, now, image))
                count += 1
        return count

    def restore_known_tokens(self):
        """Preserve missed candidates from v2's old tables without old logic."""
        tables = {row[0] for row in self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        now = time.time()
        with self.connection:
            if 'market_queue' in tables:
                self.connection.execute('''INSERT OR IGNORE INTO candidates
                    (mint,first_seen,last_seen,source,last_source,priority,next_scan)
                    SELECT mint,COALESCE(first_seen,?),?,
                      'legacy_inventory','legacy_inventory',0,?
                      FROM market_queue''', (now, now, now))
            elif 'tokens' in tables:
                self.connection.execute('''INSERT OR IGNORE INTO candidates
                    (mint,first_seen,last_seen,source,last_source,priority,next_scan)
                    SELECT mint,COALESCE(created_at,?),?,
                      'legacy_tokens','legacy_tokens',0,?
                      FROM tokens WHERE stage='migrated' ''', (now, now, now))

    def due(self, limit: int = 30) -> list[str]:
        """60% active + 40% old candidates; no arbitrary total queue cap."""
        now = time.time()
        hot_count = round(limit * .6)
        query = '''SELECT mint FROM candidates WHERE next_scan<=?
                   AND mint NOT IN (SELECT mint FROM alerts
                     WHERE state!='rejected' OR decided_at>?)
                   AND priority {comparison} ORDER BY next_scan, first_seen LIMIT ?'''
        hot = [r[0] for r in self.connection.execute(
            query.format(comparison='>=2'), (now, now - 60, hot_count))]
        cold = [r[0] for r in self.connection.execute(
            query.format(comparison='<2'), (now, now - 60, limit - len(hot)))]
        selected = hot + cold
        if len(selected) < limit:
            for row in self.connection.execute('''SELECT mint FROM candidates
                WHERE next_scan<=? AND mint NOT IN (SELECT mint FROM alerts
                  WHERE state!='rejected' OR decided_at>?)
                ORDER BY next_scan,first_seen LIMIT ?''', (now, now - 60, limit)):
                if row[0] not in selected:
                    selected.append(row[0])
                if len(selected) >= limit:
                    break
        # Reserve now, before networking, to avoid selecting duplicate batches.
        with self.connection:
            self.connection.executemany('''UPDATE candidates SET next_scan=?,
                last_scan=?, scans=scans+1 WHERE mint=?''',
                [(now + 25, now, mint) for mint in selected])
        return selected

    def result(self, mint: str, snapshot: MarketSnapshot | None,
               decision: Decision | None = None, *, unavailable: bool = False):
        now = time.time()
        if unavailable:
            delay, priority, reason = 30, 1, 'market provider unavailable'
        elif snapshot is None:
            last = self.connection.execute(
                'SELECT first_seen,scans FROM candidates WHERE mint=?', (mint,)).fetchone()
            if not last:
                return
            age = now - last['first_seen']
            delay = 25 if age < 180 else min(300, 45 + 5 * last['scans'])
            priority, reason = (2 if age < 180 else 0), 'DEX pool not indexed'
        else:
            moving = (snapshot.buys_5m >= 8
                      and snapshot.buys_5m >= 1.2 * max(1, snapshot.sells_5m)
                      and snapshot.change_5m >= 1)
            delay = 12 if moving else 90
            priority = 2 if moving else 0
            reason = decision.reason if decision else 'snapshot read'
        with self.connection:
            if snapshot is None:
                self.connection.execute('''UPDATE candidates SET next_scan=?,
                    priority=?,last_reason=? WHERE mint=?''',
                    (now + delay, priority, reason, mint))
            else:
                self.connection.execute('''UPDATE candidates SET next_scan=?,
                    priority=?,last_reason=?, first_market_at=COALESCE(first_market_at,?),
                    first_market_cap=COALESCE(first_market_cap,?),
                    last_market_at=?,last_market_cap=?,last_volume_5m=?,
                    last_change_5m=?,symbol=?,name=?,
                    image_url=COALESCE(image_url,?) WHERE mint=?''',
                    (now + delay, priority, reason, now, snapshot.market_cap,
                     now, snapshot.market_cap, snapshot.volume_5m,
                     snapshot.change_5m, snapshot.symbol, snapshot.name,
                     snapshot.image, mint))

    def reserve_alert(self, mint: str, decision: Decision) -> bool:
        with self.connection:
            cursor = self.connection.execute('''INSERT INTO alerts
                (mint,decided_at,state,reason,score) VALUES(?,?,'sending',?,?)
                ON CONFLICT(mint) DO UPDATE SET
                  decided_at=excluded.decided_at,state=excluded.state,
                  reason=excluded.reason,score=excluded.score,message_id=NULL,note=''
                WHERE alerts.state='rejected'
                  AND alerts.decided_at < excluded.decided_at - 60''',
                (mint, time.time(), decision.reason, decision.strength))
        return cursor.rowcount > 0

    def alert_result(self, mint: str, state: str, message_id: int | None,
                     note: str = ''):
        with self.connection:
            self.connection.execute('''UPDATE alerts SET state=?,message_id=?,
                sent_at=?,note=? WHERE mint=?''',
                (state, message_id, time.time() if state == 'sent' else None,
                 note[:180], mint))

    def stats(self) -> dict:
        result = {
            'candidates': self.connection.execute('SELECT COUNT(*) FROM candidates').fetchone()[0],
            'due_now': self.connection.execute('''SELECT COUNT(*) FROM candidates
                WHERE next_scan<=? AND mint NOT IN (SELECT mint FROM alerts
                  WHERE state!='rejected' OR decided_at>?)''',
                (time.time(), time.time() - 60)).fetchone()[0],
            'alerts': {row[0]: row[1] for row in self.connection.execute(
                'SELECT state,COUNT(*) FROM alerts GROUP BY state')},
            'counters': {row[0]: row[1] for row in self.connection.execute(
                'SELECT name,value FROM counters')},
        }
        return result

    def trace(self, mint: str) -> dict:
        candidate = self.connection.execute('SELECT * FROM candidates WHERE mint=?',
                                            (mint,)).fetchone()
        alert = self.connection.execute('SELECT * FROM alerts WHERE mint=?',
                                        (mint,)).fetchone()
        return {'mint': mint, 'candidate': dict(candidate) if candidate else None,
                'alert': dict(alert) if alert else None}
