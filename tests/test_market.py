import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from helpers import MINT
from solter.chain import migration_pool
from solter.config import Config
from solter.engine import Solter
from solter.market import by_mint, gecko_mints, market_ready, market_snapshot
from solter.reservoir import blocker
from solter.storage import Store


def pair(now, buys=23, sells=6, volume=3400, change=18, dex='pumpswap'):
    return {'chainId': 'solana', 'dexId': dex,
            'baseToken': {'address': MINT, 'symbol': 'MOMO', 'name': 'Momo'},
            'pairAddress': migration_pool(MINT), 'pairCreatedAt': (now - 3*86400)*1000,
            'liquidity': {'usd': 19000}, 'volume': {'m5': volume},
            'priceChange': {'m5': change}, 'txns': {'m5': {'buys': buys, 'sells': sells}},
            'marketCap': 18000}


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'live.sqlite')
        self.now = time.time()
        self.bot = Solter(Config(call_mode='cepat', scanner_mode='chain'), self.store)
        self.mint_feed = {'data': [{'relationships': {'base_token':
                                     {'data': {'id': 'solana_' + MINT}}}}]}

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_feed_validated_and_not_paid_profile_trust(self):
        self.assertEqual(gecko_mints(self.mint_feed), [MINT])
        self.assertEqual(gecko_mints({'data': [{'relationships': {'base_token':
                            {'data': {'id': 'eth_ABC'}}}}]}), [])
        self.assertEqual(by_mint([pair(self.now, dex='raydium')], {MINT}), {})
        self.assertEqual(set(by_mint([pair(self.now)], {MINT})), {MINT})

    def test_old_pool_revives_on_fresh_momentum(self):
        p = pair(self.now)
        snap = market_snapshot(p, self.now)
        self.assertTrue(market_ready(snap, self.now, 7, 650, 5000))
        self.store.update_market(MINT, p['pairAddress'], snap, self.now)
        token = self.store.token(MINT)
        self.assertLess(token['created_at'], self.now - 2 * 86400)
        self.assertIsNone(blocker(token, self.now, self.bot.reservoir, 0))
        self.bot.schedule(self.now)
        self.assertEqual(self.store.token(MINT)['state'], 'watching')

    def test_no_chasing_sell_dominant_pool(self):
        p = pair(self.now, buys=12, sells=90)
        snap = market_snapshot(p, self.now)
        self.store.update_market(MINT, p['pairAddress'], snap, self.now)
        self.assertEqual(blocker(self.store.token(MINT), self.now, self.bot.reservoir, 0),
                         'market momentum missing')
        self.bot.schedule(self.now)
        self.assertEqual(self.store.token(MINT)['state'], 'alive')

    def test_market_scanner_batches_and_reuses_old_data(self):
        async def fake_http(url, limit=2**21):
            return self.mint_feed

        async def fake_dex(path):
            return [pair(self.now)] if path.startswith('/tokens/v1/solana/') else []

        self.bot.http_json = fake_http
        self.bot.dex = fake_dex
        asyncio.run(self.bot.scan_market(self.now))
        self.assertEqual(self.store.token(MINT)['stage'], 'migrated')
        self.assertEqual(self.store.metrics()['market_discovered'], 1)
        self.assertEqual(self.bot.pairs[MINT][1]['address'], migration_pool(MINT))
        asyncio.run(self.bot.scan_market(self.now + 67))
        self.assertEqual(self.store.metrics()['market_updated'], 1)

    def test_existing_db_migration_retains_history(self):
        self.store.update_market(MINT, migration_pool(MINT),
                                 market_snapshot(pair(self.now), self.now), self.now)
        self.store.close()
        self.store = Store(Path(self.tmp.name) / 'live.sqlite')
        self.assertEqual(self.store.token(MINT)['market_buys'], 23)
        self.assertIn('market_mcap_usd', self.store.columns)


if __name__ == '__main__':
    unittest.main()
