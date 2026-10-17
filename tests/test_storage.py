import tempfile
import time
import unittest
from pathlib import Path

from helpers import CREATOR, MINT, addr
from solter.events import Trade
from solter.storage import Store


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Store(Path(self.tmp.name) / 's.sqlite3')
        self.now = time.time()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def add(self, mint=MINT, creator=CREATOR, ago=100):
        return self.db.discover({'mint': mint, 'symbol': 'AAA', 'name': 'Coin', 'creator': creator,
                                 'dev_sol': 1, 'dev_tokens': 3e7}, addr('curve-' + mint), self.now - ago)

    def test_discovery_counts_creator_launches(self):
        self.assertTrue(self.add())
        self.assertFalse(self.add())                        # same mint twice
        self.add(mint=addr('m2'))
        self.assertEqual(self.db.launches(CREATOR), 2)
        self.assertEqual(self.db.launches(None), 0)

    def test_sweep_batch_prefers_oldest_unchecked_to_prevent_starvation(self):
        self.add(mint=addr('old'), ago=300)
        self.add(mint=addr('new'), ago=60)
        self.add(mint=addr('baby'), ago=5)                  # too young for a first look
        self.add(mint=addr('hot'), ago=400)
        self.db.update(addr('hot'), state='alive', traction=5, checks=1)
        batch = [t['mint'] for t in self.db.sweep_batch(self.now, 10, 45, 90)]
        self.assertEqual(batch, [addr('old'), addr('new'), addr('hot')])

    def test_watch_cycle(self):
        self.add()
        self.assertFalse(self.db.promote(MINT, self.now, 300))   # only alive tokens
        self.db.update(MINT, state='alive')
        self.assertTrue(self.db.promote(MINT, self.now, 300))
        self.assertEqual([t['mint'] for t in self.db.subscriptions()], [MINT])
        trade = Trade(addr('b'), 'buy', 10, .5, self.now - 3, 2e-8, .2, 7, 0)
        self.assertEqual(self.db.add_trades(MINT, 'sig', [trade]), 1)
        self.assertEqual(self.db.add_trades(MINT, 'sig', [trade]), 0)
        token = self.db.token(MINT)
        self.assertEqual((token['first_price'], token['last_sig']), (2e-8, 'sig'))
        self.assertAlmostEqual(token['progress'], 20)
        self.assertEqual(self.db.trades(MINT)[0].sol, .5)
        self.db.demote(MINT, self.now, 300, 'quiet')
        token = self.db.token(MINT)
        self.assertEqual((token['state'], token['reason']), ('alive', 'quiet'))
        self.assertEqual(self.db.add_trades(MINT, 'sig2', [trade]), 0)   # not watched any more

    def test_migration(self):
        self.add()
        self.db.update(MINT, state='watching')
        self.db.add_trades(MINT, 's', [Trade('w', 'buy', 1, 1, self.now)])
        self.assertEqual(self.db.migrate(MINT, 'pool', self.now), 'watching')
        token = self.db.token(MINT)
        self.assertEqual((token['stage'], token['account'], token['state']), ('migrated', 'pool', 'watching'))
        self.assertEqual(self.db.trades(MINT), [])
        self.assertEqual(self.db.migrate(MINT, 'pool', self.now), 'repeat')
        self.assertEqual(self.db.migrate(addr('stranger'), 'pool2', self.now), 'unknown')
        self.assertEqual(self.db.token(addr('stranger'))['state'], 'alive')
        self.add(mint=addr('called'))
        self.db.claim_call(addr('called'), 'new', 5, self.now)
        self.db.update(addr('called'), state='expired')
        self.db.migrate(addr('called'), 'pool3', self.now)
        self.assertEqual(self.db.token(addr('called'))['state'], 'expired')   # never revived

    def test_one_call_per_mint_and_hourly_cap(self):
        self.assertTrue(self.db.claim_call(MINT, 'new', 2, self.now))
        self.assertFalse(self.db.claim_call(MINT, 'migrated', 2, self.now))
        self.assertTrue(self.db.claim_call(addr('b'), 'new', 2, self.now))
        self.assertFalse(self.db.claim_call(addr('c'), 'new', 2, self.now))
        self.db.finish_call(MINT, 'sent', 42, {'score': 80, 'grade': 'A', 'bogus': 1})
        call = self.db.call(MINT)
        self.assertEqual((call['state'], call['message_id'], call['score']), ('sent', 42, 80))

    def test_outcome_tracks_peak_and_low_after_the_call_only(self):
        self.db.claim_call(MINT, 'new', 5, self.now)
        self.db.open_outcome(MINT, 1e-8, self.now)
        self.db.track(MINT, 9e-8, self.now - 10)              # before the call: ignored
        self.db.track(MINT, 3e-8, self.now + 60)
        self.db.track(MINT, .5e-8, self.now + 120)
        self.db.track(MINT, 2e-8, self.now + 90)              # late arrival: peak/low only
        o = self.db.outcome(MINT)
        self.assertEqual((o['peak'], o['low'], o['last']), (3e-8, .5e-8, .5e-8))

    def test_expiry_and_missed_runners(self):
        self.add(mint=addr('stale'), ago=7200)
        self.add(mint=addr('unchecked'), ago=700)
        self.add(mint=addr('runner'), ago=600)
        self.db.update(addr('runner'), checks=2, state='alive', first_mcap_sol=40, peak_mcap_sol=200,
                       reason='few buyers')
        self.assertEqual(self.db.expire(self.now, 60), 2)
        self.assertEqual(self.db.token(addr('unchecked'))['reason'], 'not checked in time')
        missed = self.db.missed(self.now - 86400, 3)
        self.assertEqual([m['mint'] for m in missed], [addr('runner')])

    def test_budget_counts_per_kind(self):
        self.db.budget_claim('sweep')
        self.db.budget_claim('sweep')
        self.db.budget_claim('decision')
        self.assertEqual(self.db.budget_by_kind(), {'sweep': 2, 'decision': 1})
        self.assertEqual(self.db.budget_used(), 3)
        self.assertEqual(self.db.stats()['rpc_today_by_kind'], {'sweep': 2, 'decision': 1})


if __name__ == '__main__':
    unittest.main()
