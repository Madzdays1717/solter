"""Regression tests: coverage, fair rotation, diagnostics, breakout, and no RPC."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from helpers import MINT, addr
from solter.config import Config
from solter.engine import Solter
from solter.market_signal import evaluate_market
from solter.storage import Store
from solter.market_inventory import MarketInventory
from test_market_only import dex_pair, FakeTelegram


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 's.sqlite'
        self.db = Store(self.path)
        self.queue = MarketInventory(self.db)
        self.now = time.time()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_large_feed_does_not_discard_after_ninety_or_hundred_twenty(self):
        mints = [addr(f'feed-{n}') for n in range(350)]
        self.queue.add(mints, 'gecko', self.now)
        scheduled = []
        for _ in range(12):
            scheduled += self.queue.due(self.now, 30)
        self.assertEqual(len(scheduled), 350)
        self.assertEqual(len(set(scheduled)), 350)

    def test_hot_token_does_not_starve_cold_inventory(self):
        hot = [addr(f'hot-{n}') for n in range(200)]
        cold = [addr(f'cold-{n}') for n in range(20)]
        self.queue.add(hot, 'migration', self.now, priority=3)
        self.queue.add(cold, 'old', self.now, priority=0)
        scheduled = self.queue.due(self.now, 30)
        self.assertGreaterEqual(len(set(scheduled) & set(cold)), 10)
        self.assertGreaterEqual(len(set(scheduled) & set(hot)), 16)

    def test_first_discovery_and_first_mcap_survive_restart(self):
        self.queue.add([MINT], 'new_pool', self.now, priority=1)
        self.assertEqual(self.queue.due(self.now), [MINT])
        self.queue.result(MINT, self.now + 3, indexed=True, token={
            'market_mcap_usd': 37000, 'market_buys': 30,
            'market_sells': 5, 'market_change_pct': 10})
        self.db.close()
        self.db = Store(self.path)
        self.queue = MarketInventory(self.db)
        trace = self.queue.trace(MINT)
        self.assertEqual(trace['first_mcap_usd'], 37000)
        self.assertEqual(trace['first_seen'], self.now)
        self.queue.result(MINT, self.now + 30, indexed=True, token={
            'market_mcap_usd': 99999, 'market_buys': 4,
            'market_sells': 3, 'market_change_pct': 0})
        self.assertEqual(self.queue.trace(MINT)['first_mcap_usd'], 37000)

    def test_transient_missing_index_retries_without_dropping(self):
        self.queue.add([MINT], 'migration', self.now, priority=3)
        self.queue.due(self.now)
        self.queue.result(MINT, self.now, indexed=False)
        self.assertEqual(self.queue.trace(MINT)['last_reason'], 'DEX not indexed yet')
        self.assertEqual(self.queue.due(self.now + 19), [])
        self.assertEqual(self.queue.due(self.now + 21), [MINT])

    def test_duplicate_feed_never_resets_cold_ttl_or_priority(self):
        self.queue.add([MINT], 'older', self.now)
        self.queue.due(self.now)
        self.queue.result(MINT, self.now, indexed=False)
        before = self.queue.trace(MINT)['due_at']
        self.queue.add([MINT], 'older', self.now + 1)
        self.assertEqual(self.queue.trace(MINT)['due_at'], before)


class MomentumV25Tests(unittest.TestCase):
    def payload(self, **kwargs):
        return {'market_at': time.time(), 'market_buys': 1596,
                'market_sells': 802, 'market_volume_usd': 271518,
                'market_liquidity_usd': 130482, 'market_change_pct': 22.41,
                'market_mcap_usd': 2240000, **kwargs}

    def test_real_reported_breakout_is_not_rejected_by_300k_ceiling(self):
        decision = evaluate_market(self.payload(), time.time())
        self.assertTrue(decision.ok, decision.reason)
        self.assertEqual(decision.reason, 'market breakout')

    def test_big_market_cap_with_weak_volume_still_rejected(self):
        decision = evaluate_market(self.payload(market_volume_usd=3000), time.time())
        self.assertFalse(decision.ok)
        self.assertEqual(decision.reason, 'market low volume')

    def test_big_market_cap_with_weak_liquidity_still_rejected(self):
        decision = evaluate_market(self.payload(market_liquidity_usd=5000), time.time())
        self.assertFalse(decision.ok)
        self.assertEqual(decision.reason, 'market thin liquidity')

    def test_overextended_small_cap_is_rejected(self):
        data = self.payload(market_mcap_usd=18000, market_change_pct=150)
        self.assertEqual(evaluate_market(data, time.time()).reason, 'market overextended')


class ScannerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Store(Path(self.tmp.name) / 's.sqlite')
        self.bot = Solter(Config(scanner_mode='market'), self.db)
        self.bot.tg = FakeTelegram()
        self.bot.sol_usd = 100.0

    async def asyncTearDown(self):
        self.db.close()
        self.tmp.cleanup()

    async def test_one_batch_is_handled_without_rpc(self):
        self.bot.market_inventory.add([MINT], 'migration', time.time(), priority=3)
        async def dex(path):
            return [dex_pair()]
        self.bot.dex = dex
        self.assertTrue(await self.bot.market_runtime.scan_once())
        self.assertEqual(len(self.bot.tg.sent), 1)
        self.assertIsNone(self.bot.rpc)
        self.assertEqual(self.bot.market_inventory.trace(MINT)['first_mcap_usd'], 18000)
        self.assertEqual(self.db.metrics()['market_polled_tokens'], 1)

    async def test_feed_ingestion_more_than_120_without_immediate_rpc(self):
        feed = [addr(f'feed-{x}') for x in range(160)]
        payload = {'data':[{'relationships':{'base_token':{'data':{'id':'solana_'+m}}}} for m in feed]}
        async def http_json(url):
            return payload
        async def dex(path):
            return []
        self.bot.http_json = http_json
        self.bot.dex = dex
        await self.bot.market_runtime.discover_once(time.time())
        count = self.db.db.execute('SELECT COUNT(*) FROM market_queue').fetchone()[0]
        self.assertEqual(count, 160)

    async def test_raydium_sol_quote_uses_unverified_market_alert(self):
        other = addr('ray-pool')
        self.bot.market_inventory.add([MINT], 'trending', time.time(), priority=2)
        p = dex_pair(dexId='raydium', pairAddress=other)
        async def dex(path):
            return [p]
        self.bot.dex = dex
        self.assertTrue(await self.bot.market_runtime.scan_once())
        self.assertEqual(len(self.bot.tg.sent), 1)
        self.assertIn('raydium', self.bot.tg.sent[0][1])
        self.assertEqual(self.db.token(MINT)['market_dex'], 'raydium')
        self.assertIsNone(self.bot.rpc)

    async def test_unknown_dex_is_not_silently_treated_as_unindexed(self):
        self.bot.market_inventory.add([MINT], 'trending', time.time(), priority=2)
        async def dex(path):
            return [dex_pair(dexId='suspicious', pairAddress=addr('odd-pool'))]
        self.bot.dex = dex
        self.assertTrue(await self.bot.market_runtime.scan_once())
        self.assertEqual(self.bot.tg.sent, [])
        self.assertEqual(self.bot.market_inventory.trace(MINT)['last_reason'], 'unsupported DEX/pool')

class NonblockingMarketTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_telegram_delivery_does_not_block_scanning_next_token(self):
        from solter.chain import migration_pool
        tmp = tempfile.TemporaryDirectory()
        store = Store(Path(tmp.name)/'db.sqlite')
        bot = Solter(Config(scanner_mode='market'), store)
        entered = asyncio.Event()
        release = asyncio.Event()
        delayed = MINT
        following = addr('nexttoken')
        class TG:
            def __init__(self):
                self.sent = []
            async def call(self, msg, mint, image):
                if mint == delayed:
                    entered.set()
                    await release.wait()
                self.sent.append(mint)
                return ('sent', 1)
        bot.tg = TG()
        bot.sol_usd = 100
        async def dex(path):
            return [dex_pair(mint=key) for key in (delayed, following)
                    if key in path]
        bot.dex = dex
        try:
            await bot.market_batch([delayed])
            await asyncio.wait_for(entered.wait(), timeout=1)
            await asyncio.wait_for(bot.market_batch([following]), timeout=1)
            self.assertIn(following, bot.tg.sent)
            self.assertNotIn(delayed, bot.tg.sent)
            release.set()
            await asyncio.gather(*list(bot.market_alert_jobs.values()))
            self.assertIn(delayed, bot.tg.sent)
        finally:
            release.set()
            if bot.market_alert_jobs:
                await asyncio.gather(*list(bot.market_alert_jobs.values()), return_exceptions=True)
            store.close()
            tmp.cleanup()
