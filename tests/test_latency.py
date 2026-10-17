import asyncio
import time
import unittest
from unittest.mock import patch

import test_engine as fixtures
from test_engine import MINT, holders
from test_io import StoreCase, FakeResponse
from solter.rpc import BoundedRPC


class LatencyTests(unittest.TestCase):
    setUp = fixtures.EngineTests.setUp
    tearDown = fixtures.EngineTests.tearDown
    launch = fixtures.EngineTests.launch
    watched = fixtures.EngineTests.watched
    buys = fixtures.EngineTests.buys

    def test_early_quality_prefetch_runs_once_without_call(self):
        self.watched()
        self.buys(n=4)
        token = self.db.token(MINT)
        asyncio.run(self.bot.consider(token, time.time()))
        before = len(self.bot.rpc.calls)
        self.assertIn(MINT, self.bot.checked)
        self.assertEqual(self.db.metrics()['quality_prefetch'], 1)
        asyncio.run(self.bot.consider(token, time.time()))
        self.assertEqual(len(self.bot.rpc.calls), before)
        self.assertFalse(self.bot.tg.sent)

    def test_holder_failure_retries_after_ten_seconds(self):
        self.watched()
        self.buys()
        self.bot.rpc.handlers['getTokenLargestAccounts'] = None
        now = time.time()
        asyncio.run(self.bot.consider(self.db.token(MINT), now))
        self.assertEqual(self.db.token(MINT)['reason'], 'holders unavailable')
        self.assertFalse(self.bot.tg.sent)
        self.bot.rpc.handlers['getTokenLargestAccounts'] = holders()
        asyncio.run(self.bot.consider(self.db.token(MINT), now + 11))
        self.assertEqual(len(self.bot.tg.sent), 1)

    def test_flow_is_rechecked_after_slow_rpc(self):
        self.watched()
        self.buys()
        now = time.time()
        with patch.object(self.bot, 'chain_now', return_value=now + 190):
            asyncio.run(self.bot.consider(self.db.token(MINT), now))
        self.assertFalse(self.bot.tg.sent)

    def test_demoted_token_cannot_call_after_rpc(self):
        self.watched()
        self.buys()
        original = self.bot.assess
        async def assess(*args):
            result = await original(*args)
            self.db.demote(MINT, time.time(), 300, 'rotated')
            return result
        self.bot.assess = assess
        asyncio.run(self.bot.consider(self.db.token(MINT), time.time()))
        self.assertFalse(self.bot.tg.sent)

    def test_empty_holders_never_pass(self):
        self.watched()
        self.buys()
        self.bot.rpc.handlers['getTokenLargestAccounts'] = {'value': []}
        asyncio.run(self.bot.consider(self.db.token(MINT), time.time()))
        self.assertEqual(self.db.token(MINT)['reason'], 'holders unavailable')
        self.assertFalse(self.bot.tg.sent)

    def test_slow_token_does_not_block_next_and_shutdown_cancels(self):
        async def scenario():
            slow = asyncio.Event()
            fast = asyncio.Event()
            async def consider(token, now):
                if token['mint'] == 'slow':
                    await slow.wait()
                else:
                    fast.set()
            self.bot.consider = consider
            with patch.object(self.db, 'watching', return_value=[{'mint': 'slow'}, {'mint': 'fast'}]):
                job = asyncio.create_task(self.bot.evaluator())
                try:
                    await asyncio.wait_for(fast.wait(), .5)
                    self.assertFalse(slow.is_set())
                finally:
                    job.cancel()
                    await asyncio.gather(job, return_exceptions=True)
                self.assertFalse(self.bot.eval_jobs)
        asyncio.run(scenario())


class RetryTests(StoreCase):
    def test_fallback_and_each_attempt_is_budgeted(self):
        class Session:
            urls = []
            def post(self, url, **kwargs):
                self.urls.append(url)
                return FakeResponse(status=503) if len(self.urls) == 1 else FakeResponse()
        session = Session()
        rpc = BoundedRPC('https://primary', self.db, 100, 100, session, 'https://backup')
        self.assertEqual(asyncio.run(rpc.call('getTokenLargestAccounts', [])), {'ok': True})
        self.assertEqual(session.urls, ['https://primary', 'https://backup'])
        self.assertEqual(self.db.budget_used(), 2)
