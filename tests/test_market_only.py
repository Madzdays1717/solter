"""Market-mode contracts: no Solana RPC calls, no fake safety claims, no spam."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from helpers import MINT, addr
from solter.chain import migration_pool
from solter.config import Config
from solter.engine import Solter
from solter.market_signal import evaluate_market
from solter.storage import Store


def dex_pair(mint=MINT, **override):
    now = time.time()
    pair = {'chainId': 'solana', 'dexId': 'pumpswap',
            'baseToken': {'address': mint, 'symbol': 'ST', 'name': '<b>Coin'},
            'quoteToken': {'address': 'So11111111111111111111111111111111111111112'},
            'pairAddress': migration_pool(mint),
            'pairCreatedAt': (now - 2 * 86400) * 1000,
            'liquidity': {'usd': 19000}, 'volume': {'m5': 3400},
            'priceChange': {'m5': 18}, 'txns': {'m5': {'buys': 23, 'sells': 6}},
            'marketCap': 18000, 'priceNative': '0.000001'}
    pair.update(override)
    return pair


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.replies = []
    async def reply(self, text, message_id, mint=None):
        self.replies.append((text, message_id, mint))
        return 'sent'
    async def call(self, text, mint, image):
        self.sent.append((mint, text, image))
        return 'sent', len(self.sent)


class MarketOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db.sqlite')
        self.bot = Solter(Config(scanner_mode='market', tg_token='x', tg_chat='y',
                                 min_mcap_usd=3000, max_mcap_usd=300000), self.store)
        self.bot.tg = FakeTelegram()
        self.bot.sol_usd = 100.0

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def batch(self, pair):
        async def dex(path):
            assert path.startswith('/tokens/v1/solana/')
            return [pair]
        self.bot.dex = dex
        await self.bot.market_batch([pair['baseToken']['address']])

    async def test_zero_rpc_workers_and_no_client(self):
        self.bot.attach(object())
        self.assertIsNone(self.bot.rpc)
        names = {x.__name__ for x in self.bot.workers_for_mode()}
        for worker in ('sweep', 'scheduler', 'logs', 'transactions',
                       'backfill', 'evaluator'):
            self.assertNotIn(worker, names)
        self.assertIn('market_fast_scanner', names)
        self.assertIn('market_scanner', names)

    async def test_old_migrated_coin_sends_unverified_alert_and_one_record(self):
        self.assertIsNone(self.bot.rpc)
        p = dex_pair()
        await self.batch(p)
        self.assertEqual(len(self.bot.tg.sent), 1)
        mint, message, image = self.bot.tg.sent[0]
        self.assertEqual(mint, MINT)
        self.assertIn('SOLTER MOMENTUM ALERT', message)
        self.assertIn('BELUM DIVERIFIKASI ON-CHAIN', message)
        self.assertIn('Beli <b>23</b>', message)
        self.assertNotIn('Mint & freeze authority dicabut', message)
        self.assertIn('&lt;b&gt;Coin', message)
        self.assertEqual(self.store.call(MINT)['grade'], 'M')
        self.assertEqual(self.store.call(MINT)['note'], 'MARKET SNAPSHOT ONLY - UNVERIFIED ON-CHAIN')
        self.assertLess(self.store.token(MINT)['created_at'], time.time() - 86400)
        self.assertEqual(self.store.metrics()['market_alert_sent'], 1)
        self.assertIsNotNone(self.store.outcome(MINT))

    async def test_market_milestone_is_professional_and_not_spam(self):
        await self.batch(dex_pair())
        before = self.store.outcome(MINT)
        self.assertIsNotNone(before)
        self.store.track(MINT, before['entry'] * 5.4, before['call_at'] + 10)
        o = self.store.open_outcomes()[0]
        await self.bot.follow(o, before['call_at'] + 25)
        self.assertEqual(len(self.bot.tg.replies), 1)
        message, original_id, mint = self.bot.tg.replies[0]
        self.assertIn('MILESTONE 5×', message)
        self.assertIn('5.40×', message)
        self.assertIn('BELUM DIVERIFIKASI ON-CHAIN', message)
        self.assertNotIn('MC puncak', message)
        self.assertEqual(mint, MINT)
        self.assertEqual(self.store.outcome(MINT)['milestone'], 5)
        await self.bot.follow(self.store.open_outcomes()[0], before['call_at'] + 30)
        self.assertEqual(len(self.bot.tg.replies), 1)
        self.assertIsNone(self.bot.rpc)

    async def test_repeated_snapshot_does_not_send_second_alert(self):
        p = dex_pair()
        await self.batch(p)
        await self.batch(p)
        self.assertEqual(len(self.bot.tg.sent), 1)
        self.assertEqual(self.store.metrics()['market_signal_confirmed'], 1)

    async def test_sell_dominated_pool_does_not_alert(self):
        p = dex_pair(txns={'m5': {'buys': 22, 'sells': 46}})
        await self.batch(p)
        self.assertEqual(self.bot.tg.sent, [])
        self.assertEqual(self.store.token(MINT)['reason'], 'market weak buy pressure')

    async def test_too_late_fomo_rise_does_not_alert(self):
        p = dex_pair(priceChange={'m5': 150})
        await self.batch(p)
        self.assertEqual(self.bot.tg.sent, [])
        self.assertEqual(self.store.token(MINT)['reason'], 'market overextended')

    async def test_unknown_mcap_does_not_alert_as_safe(self):
        p = dex_pair(marketCap=None)
        await self.batch(p)
        self.assertEqual(self.bot.tg.sent, [])
        self.assertEqual(self.store.token(MINT)['reason'], 'market data incomplete')

    async def test_non_sol_quote_rejected_without_chain_verification(self):
        await self.batch(dex_pair(quoteToken={'address': addr('usdc')}))
        self.assertIsNone(self.store.token(MINT))
        self.assertEqual(self.bot.tg.sent, [])
        self.assertEqual(self.store.metrics()['market_non_sol_quote'], 1)

    async def test_noncanonical_pool_rejected_in_market_mode(self):
        await self.batch(dex_pair(pairAddress=addr('not-canonical')))
        self.assertIsNone(self.store.token(MINT))
        self.assertEqual(self.bot.tg.sent, [])
        self.assertEqual(self.store.metrics()['market_noncanonical_pool'], 1)

    async def test_wrong_dex_cannot_pass_as_pumpswap(self):
        await self.batch(dex_pair(dexId='unknown-pool'))
        self.assertIsNone(self.store.token(MINT))
        self.assertEqual(self.bot.tg.sent, [])

    async def test_hourly_alert_cap(self):
        self.bot.cfg = Config(scanner_mode='market', max_calls_per_hour=1,
                              min_mcap_usd=3000, max_mcap_usd=300000)
        await self.batch(dex_pair())
        other = addr('another-mint')
        await self.batch(dex_pair(other))
        self.assertEqual(len(self.bot.tg.sent), 1)
        self.assertEqual(self.store.token(other)['reason'], 'hourly call cap')

    async def test_migration_is_retried_without_rpc_when_dex_is_late(self):
        now = time.time()
        self.bot.on_migration(MINT, now)
        self.assertEqual(self.bot.market_inventory.trace(MINT)['source'], 'pumpportal_migration')
        self.assertTrue(self.bot.market_fast_wakeup.is_set())
        self.assertIn(MINT, self.bot.market_inventory.due(now, 30))
        self.bot.market_inventory.result(MINT, now, indexed=False)
        self.assertLessEqual(self.bot.market_inventory.trace(MINT)['due_at'], now + 21)
        self.assertIn(MINT, self.store.hot_market_candidates(now + 60))

    async def test_market_snapshot_stale_and_missing_values(self):
        info = self.store.token(MINT)
        self.assertFalse(evaluate_market({'market_at': time.time() - 120}, time.time()).ok)
        self.assertEqual(evaluate_market({'market_at': time.time()}, time.time()).reason,
                         'market data incomplete')

    async def test_chain_legacy_mode_remains_available(self):
        old = Solter(Config(scanner_mode='chain'), self.store)
        workers = {x.__name__ for x in old.workers_for_mode()}
        self.assertIn('sweep', workers)
        self.assertIn('logs', workers)
        self.assertNotIn('market_fast_scanner', workers)

    async def test_env_switch_and_user_rules(self):
        config = Config.load({'SCANNER_MODE': 'market', 'CALL_MODE': 'cepat',
                              'MARKET_SIGNAL_MIN_BUYS_5M': '18'})
        self.assertEqual(config.scanner_mode, 'market')
        self.assertEqual(config.market_signal_min_buys, 18)
        with self.assertRaises(ValueError):
            Config.load({'SCANNER_MODE': 'fake'})
        with self.assertRaises(ValueError):
            Config.load({'SCANNER_MODE': 'market', 'MARKET_ENABLED': 'false'})


if __name__ == '__main__':
    unittest.main()
