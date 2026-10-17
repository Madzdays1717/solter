import unittest

from helpers import CREATOR, MINT
from solter.chain import decode_curve
from solter.reservoir import ReservoirRules, blocker, candidates, classify, dev_buy_pct

from helpers import curve_bytes

NOW = 1_800_000_000.0
RULES = ReservoirRules()


def curve(real_sol, **kw):
    return decode_curve(curve_bytes(rsol=int(real_sol * 1e9), vsol=int((30 + real_sol) * 1e9), **kw))


def token(**kw):
    base = {'mint': MINT, 'state': 'fresh', 'stage': 'new', 'created_at': NOW - 120, 'dev_sol': 1.0,
            'checks': 0, 'checked_at': None, 'real_sol': None, 'creator': CREATOR, 'dev_tokens': 30e6,
            'promotions': 0, 'cooldown_until': 0, 'progress': 10, 'traction': 0}
    base.update(kw)
    return base


class ReservoirTests(unittest.TestCase):
    def test_first_check_rates_inflow_since_launch(self):
        up = classify(token(), curve(5), NOW, RULES)
        self.assertEqual(up['state'], 'alive')
        self.assertAlmostEqual(up['traction'], 4 / 2)       # 4 SOL from others over two minutes
        self.assertEqual(up['first_mcap_sol'], up['mcap_sol'])

    def test_quiet_token_gets_one_more_look_then_dies(self):
        up = classify(token(), curve(1.1), NOW, RULES)
        self.assertEqual(up['state'], 'fresh')
        t = token(**up)
        self.assertEqual(classify(t, curve(1.2), NOW + 90, RULES)['state'], 'dead')

    def test_growth_between_sweeps(self):
        t = token(state='alive', checks=1, checked_at=NOW - 60, real_sol=4, peak_real_sol=4)
        up = classify(t, curve(7), NOW, RULES)
        self.assertAlmostEqual(up['traction'], 3)
        self.assertEqual(up['stalls'], 0)

    def test_dump_and_stall(self):
        t = token(state='alive', checks=3, checked_at=NOW - 60, real_sol=10, peak_real_sol=10)
        self.assertEqual(classify(t, curve(3), NOW, RULES)['reason'], 'dumped')
        t = token(state='alive', checks=3, checked_at=NOW - 60, real_sol=5, peak_real_sol=5, stalls=4)
        self.assertEqual(classify(t, curve(5), NOW, RULES)['reason'], 'stalled')

    def test_unusable_curves(self):
        self.assertEqual(classify(token(), curve(5, mayhem=True), NOW, RULES)['state'], 'dead')
        self.assertEqual(classify(token(), curve(5, quote=MINT), NOW, RULES)['reason'], 'non-SOL quote')
        self.assertNotIn('state', classify(token(), None, NOW, RULES))
        self.assertEqual(classify(token(checks=2), None, NOW, RULES)['state'], 'dead')

    def test_candidates_rank_by_inflow_and_respect_blockers(self):
        fast = token(mint='fast', state='alive', real_sol=6, traction=3)
        slow = token(mint='slow', state='alive', real_sol=6, traction=1)
        crawl = token(mint='crawl', state='alive', real_sol=6, traction=.1)
        cooling = token(mint='cool', state='alive', real_sol=6, traction=9, cooldown_until=NOW + 10)
        late = token(mint='late', state='alive', real_sol=6, traction=9, progress=95)
        whale_dev = token(mint='dev', state='alive', real_sol=6, traction=9, dev_tokens=300e6)
        migrated = token(mint='mig', state='alive', stage='migrated', migrated_at=NOW - 60)
        old_migration = token(mint='old', state='alive', stage='migrated', migrated_at=NOW - 3600)
        ranked = candidates([slow, crawl, fast, cooling, late, whale_dev, migrated, old_migration],
                            NOW, RULES, lambda creator: 1)
        self.assertEqual([t['mint'] for t in ranked], ['mig', 'fast', 'slow'])
        self.assertEqual(blocker(fast, NOW, RULES, launches=9), 'serial creator')
        self.assertEqual(blocker(token(state='alive', real_sol=6, traction=3, promotions=3), NOW, RULES, 1),
                         'watched enough')

    def test_dev_buy_units(self):
        self.assertAlmostEqual(dev_buy_pct({'dev_tokens': 50e6}), 5)
        self.assertAlmostEqual(dev_buy_pct({'dev_tokens': 50e12}), 5)    # raw units
        self.assertIsNone(dev_buy_pct({'dev_tokens': None}))


if __name__ == '__main__':
    unittest.main()
