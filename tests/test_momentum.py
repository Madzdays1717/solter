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
from solter.momentum import pick_pairs, signal, selective_signal, confirm_progress
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

    def test_selective_gate_rejects_noise_and_missing_liquidity(self):
        cfg = Config()
        self.assertIsNone(selective_signal(pair(), None, cfg))  # old spam threshold
        strong = pair(mc=9000, vol=8000, buys=44, sells=10, change=12)
        strong['liquidity']['usd'] = 6000
        self.assertIsNotNone(selective_signal(strong, None, cfg))
        too_volatile = {**strong, 'priceChange': {'m5': 90}}
        self.assertIsNone(selective_signal(too_volatile, None, cfg))
        no_liquidity = {**strong, 'liquidity': {}}
        self.assertIsNone(selective_signal(no_liquidity, None, cfg))
        wash_risk = {**strong, 'volume': {'m5': 90000}}
        self.assertIsNone(selective_signal(wash_risk, None, cfg))
        too_small = {**strong, 'marketCap': 3000}
        self.assertIsNone(selective_signal(too_small, None, cfg))
        # Legacy .env low thresholds cannot downgrade strict safety floors.
        loose = Config(radar_min_volume_5m_usd=0, radar_min_buys_5m=1,
                       radar_min_buy_ratio=1, radar_min_mc_change_pct=0)
        self.assertIsNone(selective_signal(pair(), None, loose))

    def test_confirmation_requires_new_buy_pressure_and_mc_growth(self):
        good = pair(mc=9400, vol=9000, buys=53, sells=12, change=14)
        good['liquidity']['usd'] = 6000
        reading = selective_signal(good, {'market_cap_usd': 9000}, Config())
        self.assertIsNotNone(reading)
        baseline = {'qualified': 1, 'mc': 9000, 'volume': 8000, 'buys': 44, 'sells': 10}
        self.assertIsNotNone(confirm_progress(reading, baseline, min_mc_rise=2,
                                              min_extra_buys=4, min_extra_volume=500))
        no_new_buys = {**baseline, 'buys': 52}
        self.assertIsNone(confirm_progress(reading, no_new_buys, min_mc_rise=2,
                                            min_extra_buys=4, min_extra_volume=500))
        fake_sell_pressure = {**baseline, 'sells': 0}
        self.assertIsNone(confirm_progress(reading, fake_sell_pressure, min_mc_rise=2,
                                            min_extra_buys=4, min_extra_volume=500))
        self.assertIsNone(confirm_progress(reading, {**baseline, 'qualified': 0},
                                            min_mc_rise=2, min_extra_buys=4,
                                            min_extra_volume=500))

    def test_batch_radar_needs_two_samples_and_dedups(self):
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
            stage = [0]
            first = pair(mc=9000, vol=8000, buys=44, sells=10, change=12)
            second = pair(mc=9400, vol=9000, buys=53, sells=12, change=14)
            first['liquidity']['usd'] = 6000
            second['liquidity']['usd'] = 6000
            async def fake_dex(path):
                requests.append(path)
                return [first if stage[0] == 0 else second]
            bot.dex = fake_dex
            self.assertEqual(asyncio.run(bot.radar_once(now + 2)), 0)
            self.assertEqual(len(bot.tg.sent), 0)
            stage[0] = 1
            self.assertEqual(asyncio.run(bot.radar_once(now + 6)), 0)  # too soon
            self.assertEqual(asyncio.run(bot.radar_once(now + 19)), 1)
            self.assertEqual(len(bot.tg.sent), 1)
            self.assertIn('KANDIDAT MOMENTUM TERKONFIRMASI', bot.tg.sent[0][0])
            self.assertEqual(len(requests), 3)  # still 30 per HTTP batch
            self.assertEqual(asyncio.run(bot.radar_once(now + 36)), 0)
            self.assertEqual(len(bot.tg.sent), 1)
        finally:
            store.close()
            tmp.cleanup()

    def test_radar_ranks_confirmed_candidates_before_sending(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 's.sqlite3')
            try:
                cfg = Config(radar_batch=30)
                bot = Solter(cfg, store)
                bot.tg = FakeTelegram()
                now = time.time()
                other = addr('best-candidate')
                for mint in (MINT, other):
                    store.discover({'mint': mint, 'symbol': 'TEST'}, addr('curve-'+mint), now)
                def strong(mint, mc, vol, buys, sells):
                    result = pair(mint=mint, mc=mc, vol=vol, buys=buys, sells=sells, change=12)
                    result['liquidity']['usd'] = 6500
                    return result
                stage = [0]
                async def dex(_):
                    return ([strong(MINT, 9000, 8000, 45, 12),
                             strong(other, 9000, 10000, 55, 10)] if stage[0] == 0 else
                            [strong(MINT, 9200, 8600, 50, 13),
                             strong(other, 10200, 12400, 73, 12)])
                bot.dex = dex
                self.assertEqual(asyncio.run(bot.radar_once(now)), 0)
                stage[0] = 1
                self.assertEqual(asyncio.run(bot.radar_once(now + 16)), 1)
                self.assertEqual(bot.tg.sent[0][1], other)
            finally:
                store.close()

    def test_global_radar_cooldown_and_hour_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 's.sqlite3')
            try:
                now = time.time()
                self.assertTrue(store.radar_claim(MINT, now, 4, 9000, 8000, 600))
                self.assertFalse(store.radar_claim(addr('another'), now + 5, 4, 9500, 8500, 600))
                self.assertTrue(store.radar_claim(addr('another'), now + 601, 4, 9500, 8500, 600))
                self.assertFalse(store.radar_claim(MINT, now + 700, 4, 9200, 9000, 0))
                self.assertFalse(store.radar_claim(addr('third'), now + 900, 2, 9200, 9000, 0))
            finally:
                store.close()

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
