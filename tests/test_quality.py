import unittest

from solter.flow import Flow
from solter.quality import (Holders, QualityRules, Socials, grade, holder_stats, market_cap_reason,
                            quality_reason, safe_url,
                            score, socials_from)

SUPPLY = 10**15


def flow(**kw):
    base = dict(ok=True, reason='ok', buys=12, sells=2, buyers=12, recent_buyers=5, burst=5,
                buy_sol=8, sell_sol=1, top_share=.15, bundle_share=0, run=.2)
    base.update(kw)
    return Flow(**base)


class QualityTests(unittest.TestCase):
    def test_holders_exclude_the_vault(self):
        rows = [{'address': 'vault', 'amount': str(600 * 10**12)}] + [
            {'address': f'h{i}', 'amount': str((20 - i) * 10**12)} for i in range(12)]
        h = holder_stats(rows, {'vault'}, SUPPLY, 15 * 10**12)
        self.assertAlmostEqual(h.largest, 2.0)
        self.assertAlmostEqual(h.top10, sum(range(11, 21)) / 10)
        self.assertAlmostEqual(h.dev, 1.5)
        self.assertIsNone(holder_stats(rows, set(), 0, None))

    def test_socials_are_sanitised(self):
        s = socials_from({'twitter': 'https://x.com/coin', 'telegram': 'https://t.me/coin',
                          'website': 'javascript:alert(1)', 'image': 'ipfs://Qm123abc'})
        self.assertEqual(s.count, 2)
        self.assertIsNone(s.website)
        self.assertEqual(s.image, 'https://ipfs.io/ipfs/Qm123abc')
        self.assertIsNone(safe_url('https://evil.com/x', ('x.com',)))
        self.assertIsNone(safe_url('https://x.com/"><script>'))
        self.assertEqual(socials_from(None), Socials())

    def test_score_rewards_spread_holders_and_a_first_launch(self):
        strong = score(flow(), Holders(12, 3, 1), Socials('https://x.com/a', 'https://t.me/a'), 1)
        weak = score(flow(buyers=6, recent_buyers=3, buy_sol=3, sell_sol=1),
                     Holders(29, 9.5, 9), Socials(), 3)
        self.assertGreaterEqual(strong, 75)
        self.assertLess(weak, 40)
        self.assertEqual(grade(strong), 'A')
        self.assertEqual(grade(weak), 'C')
        self.assertTrue(0 <= weak <= strong <= 100)

    def test_market_cap_window(self):
        rules = QualityRules(min_mcap_usd=6000, max_mcap_usd=80000)
        self.assertEqual(market_cap_reason(rules, 30, 150), 'mcap too low')       # $4.5K
        self.assertIsNone(market_cap_reason(rules, 100, 150))                      # $15K
        self.assertEqual(market_cap_reason(rules, 600, 150), 'mcap too high')     # $90K
        self.assertIsNone(market_cap_reason(rules, 600, None))                     # SOL price unknown
        self.assertIsNone(market_cap_reason(QualityRules(), 600, 150))             # no window set

    def test_quality_gates(self):
        rules = QualityRules()
        ok = Holders(20, 5, 2)
        self.assertIsNone(quality_reason(rules, ok, Socials(), 1, 3))
        self.assertEqual(quality_reason(rules, Holders(45, 5, 2), Socials(), 1, 3), 'top holders concentrated')
        self.assertEqual(quality_reason(rules, Holders(20, 15, 2), Socials(), 1, 3), 'whale holder')
        self.assertEqual(quality_reason(rules, Holders(20, 5, 12), Socials(), 1, 3), 'dev holds too much')
        self.assertEqual(quality_reason(rules, ok, Socials(), 1, 30), 'dev bought too much')
        self.assertEqual(quality_reason(rules, ok, Socials(), 5, 3), 'serial creator')
        self.assertEqual(quality_reason(QualityRules(require_socials=True), ok, Socials(), 1, 3), 'no socials')
        self.assertEqual(quality_reason(rules, None, Socials(), 1, 3), 'holders unknown')


if __name__ == '__main__':
    unittest.main()
