"""Unit-level deterministic tests; do not consume live external quotas."""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from helpers import MINT, addr
from solter.config import Config
from solter.engine import Solter
from solter.momentum import pick_pairs, signal
from solter.storage import Store, SCHEMA
from test_engine import FakeTelegram


def pair(mint=MINT, mc=6500, vol=1800, buys=18, sells=5, change=8):
    return {'chainId': 'solana', 'dexId': 'pumpfun',
            'baseToken': {'address': mint}, 'quoteToken': {'address': addr('quote')},
            'marketCap': mc, 'liquidity': {'usd': 3000}, 'volume': {'m5': vol},
            'txns': {'m5': {'buys': buys, 'sells': sells}},
            'priceChange': {'m5': change}}


RULES = dict(min_mc=3000, max_mc=150000, min_volume=750, min_buys=8,
             min_buy_ratio=1.2, min_change_pct=4)


class MomentumTests(unittest.TestCase):
    def test_identifies_active_volume_and_mcap(self):
        found = signal(pair(), None, **RULES)
        self.assertEqual((found.market_cap_usd, found.buys_5m), (6500, 18))
        self.assertIsNone(signal(pair(vol=80), None, **RULES))
        self.assertIsNone(signal(pair(sells=30), None, **RULES))
        self.assertIsNone(signal(pair(mc=180000), None, **RULES))
        self.assertIsNone(signal(pair(change=-2), None, **RULES))
        self.assertIsNotNone(signal(pair(mc=7000, change=0),
                                    {'market_cap_usd': 6500}, **RULES))
        # A stale positive 5m price change must not override a sharp MC drop.
        self.assertIsNone(signal(pair(mc=6000, change=12),
                                 {'market_cap_usd': 6500}, **RULES))

    def test_pair_selection_uses_exact_base_mint_and_liquidity(self):
        low = {**pair(), 'liquidity': {'usd': 100}}
        high = {**pair(), 'liquidity': {'usd': 8000}}
        wrong_quote = {**pair(addr('other')), 'quoteToken': {'address': MINT},
                       'liquidity': {'usd': 100000}}
        chosen = pick_pairs([low, wrong_quote, high], {MINT})
        self.assertEqual(chosen[MINT]['liquidity']['usd'], 8000)
        self.assertEqual(len(chosen), 1)

    def test_batch_radar_reaches_fresh_without_watch_slot_and_dedups(self):
        tmp = tempfile.TemporaryDirectory()
        store = Store(Path(tmp.name) / 's.sqlite3')
        try:
            cfg = Config(radar_batch=30, radar_interval=4)
            bot = Solter(cfg, store)
            bot.tg = FakeTelegram()
            now = time.time()
            for i in range(30):
                mint = MINT if i == 0 else addr('mint-' + str(i))
                store.discover({'mint': mint, 'symbol': 'TEST'}, addr('curve-' + str(i)), now)
            self.assertFalse(store.watching())
            requests = []
            async def fake_dex(path):
                requests.append(path)
                return [pair(mint=MINT)]
            bot.dex = fake_dex
            self.assertEqual(asyncio.run(bot.radar_once(now + 2)), 1)
            self.assertEqual(len(bot.tg.sent), 1)
            self.assertIn('MOMENTUM AWAL', bot.tg.sent[0][0])
            self.assertEqual(len(requests), 1)  # 30 tokens in one HTTP request
            self.assertEqual(asyncio.run(bot.radar_once(now + 5)), 0)
            self.assertEqual(len(bot.tg.sent), 1)
        finally:
            store.close()
            tmp.cleanup()

    def test_existing_database_upgrade_and_radar_round_robin(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'old.sqlite3'
            # Emulate v2.0.2 by removing only the three new token columns.
            old_schema = SCHEMA.replace('  radar_seen_at REAL, radar_mc_usd REAL, radar_volume_5m_usd REAL,\n', '')
            with sqlite3.connect(path) as db:
                db.executescript(old_schema)
                db.execute('INSERT INTO tokens(mint, created_at, state, account) VALUES(?,?,?,?)',
                           (MINT, time.time(), 'fresh', addr('curve')))
            store = Store(path)
            try:
                self.assertIn('radar_seen_at', store.columns)
                self.assertEqual(store.token(MINT)['mint'], MINT)
                store.radar_mark(MINT, time.time(), 6500, 1000)
                self.assertEqual(store.token(MINT)['radar_mc_usd'], 6500)
            finally:
                store.close()
