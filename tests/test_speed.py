"""Regression checks for real-time dispatch and RPC priority."""
import asyncio
import time
import unittest
from collections import deque

from solter.config import Config
from solter.engine import Solter
from solter.rpc import BACKFILL, DECISION, BoundedRPC


class WatchingOnly:
    def watching(self):
        return [{'mint': 'slow-mint', 'last_trade_at': 1.0},
                {'mint': 'fast-mint', 'last_trade_at': 2.0}]


class FakeBudget:
    def __init__(self):
        self.count = {}

    def budget_by_kind(self):
        return self.count.copy()

    def budget_claim(self, kind):
        self.count[kind] = self.count.get(kind, 0) + 1


class SpeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_token_does_not_block_fast_token(self):
        bot = Solter(Config(), WatchingOnly())
        hanging = asyncio.Event()
        fast = asyncio.Event()

        async def check(token, _now):
            if token['mint'] == 'slow-mint':
                await hanging.wait()  # simulates stalled RPC
            else:
                fast.set()

        bot.consider = check
        loop = asyncio.create_task(bot.evaluator())
        try:
            await asyncio.wait_for(fast.wait(), timeout=.4)
            self.assertIn('slow-mint', bot.eval_jobs)
        finally:
            bot.running = False
            bot.eval_wakeup.set()
            await asyncio.wait_for(loop, timeout=1)
        self.assertEqual(bot.eval_jobs, {})

    async def test_security_calls_overtake_backfill_at_minute_ceiling(self):
        budget = FakeBudget()
        rpc = BoundedRPC('https://example.invalid', budget, 1, 1000, None)
        rpc.recent = deque([time.monotonic() - 59.80])
        backfill = asyncio.create_task(rpc._take(BACKFILL))
        await asyncio.sleep(.015)  # queue backfill first, while capacity exhausted
        decision = asyncio.create_task(rpc._take(DECISION))
        try:
            await asyncio.wait_for(decision, timeout=1.0)
            self.assertEqual(budget.count.get(DECISION), 1)
            self.assertFalse(backfill.done())
        finally:
            backfill.cancel()
            await asyncio.gather(backfill, return_exceptions=True)

    async def test_direct_event_dispatch_without_evaluator_loop(self):
        class DB:
            def token(self, mint):
                return {'mint': mint, 'state': 'watching'}
            def has_call(self, mint):
                return False
            def metric(self, key):
                pass
        bot = Solter(Config(), DB())
        done = asyncio.Event()
        async def consider(token, now):
            done.set()
        bot.consider = consider
        bot.kick_evaluation('instant-mint')
        await asyncio.wait_for(done.wait(), .15)
        await asyncio.sleep(0)
        self.assertNotIn('instant-mint', bot.eval_jobs)

    async def test_event_during_slow_check_gets_rechecked_not_lost(self):
        class DB:
            def __init__(self):
                self.events = []
            def token(self, mint):
                return {'mint': mint, 'state': 'watching'}
            def has_call(self, mint):
                return False
            def metric(self, key):
                self.events.append(key)
        db = DB()
        bot = Solter(Config(), db)
        first_started, release, second = asyncio.Event(), asyncio.Event(), asyncio.Event()
        attempts = []
        async def consider(token, now):
            attempts.append(token['mint'])
            if len(attempts) == 1:
                first_started.set()
                await release.wait()       # represents slow RPC decision
            else:
                second.set()
        bot.consider = consider
        bot.kick_evaluation('mint')
        await asyncio.wait_for(first_started.wait(), .2)
        bot.kick_evaluation('mint')  # a trade arrives during an in-flight check
        self.assertEqual(len(bot.eval_jobs), 1)
        release.set()
        await asyncio.wait_for(second.wait(), .2)
        await asyncio.sleep(0)
        self.assertEqual(len(attempts), 2)
        self.assertIn('evaluation_event_replayed', db.events)

    async def test_scheduler_reacts_to_wake_without_three_second_poll(self):
        bot = Solter(Config(), object())
        fired = asyncio.Event()
        calls = []
        def schedule(now):
            calls.append(now)
            if len(calls) == 2:
                fired.set()
        bot.schedule = schedule
        task = asyncio.create_task(bot.scheduler())
        try:
            await asyncio.sleep(.02)
            bot.scheduler_wakeup.set()
            await asyncio.wait_for(fired.wait(), .15)
        finally:
            bot.running = False
            bot.scheduler_wakeup.set()
            await task



if __name__ == '__main__':
    unittest.main()
