import unittest

from helpers import CREATOR, addr
from solter.events import Trade
from solter.flow import Rules, evaluate

NOW = 1_800_000_000.0


def buy(i, sol=.5, ago=None, slot=None, price=None, wallet=None):
    at = NOW - (ago if ago is not None else 100 - i * 9)
    return Trade(wallet or addr(i), 'buy', 1000, sol, at, price, None, slot if slot is not None else i, 0)


def sell(wallet, sol, ago=5):
    return Trade(wallet, 'sell', 1000, sol, NOW - ago, None, None, 999, 0)


class FlowTests(unittest.TestCase):
    def healthy(self):
        return [buy(i) for i in range(10)]

    def test_spread_out_real_buying_passes(self):
        flow = evaluate(self.healthy(), NOW)
        self.assertTrue(flow.ok, flow.reason)
        self.assertEqual(flow.buyers, 10)
        self.assertAlmostEqual(flow.net_sol, 5)

    def test_dust_does_not_count(self):
        rows = [buy(i, sol=.01) for i in range(30)]
        self.assertEqual(evaluate(rows, NOW).reason, 'few buys')

    def test_creator_buys_are_not_demand_and_a_creator_sale_blocks(self):
        rows = self.healthy()
        self.assertTrue(evaluate(rows, NOW, creator=CREATOR).ok)
        rows.append(sell(CREATOR, .1, ago=900))       # long before the window still counts
        self.assertEqual(evaluate(rows, NOW, creator=CREATOR).reason, 'creator sold')

    def test_one_wallet_split_is_caught_by_sol_share(self):
        rows = self.healthy() + [buy(20, sol=6, ago=30)]
        self.assertEqual(evaluate(rows, NOW).reason, 'one wallet dominates')

    def test_net_inflow_and_sell_pressure(self):
        small = [buy(i, sol=.1) for i in range(10)]
        self.assertEqual(evaluate(small, NOW).reason, 'weak net inflow')
        rows = self.healthy() + [sell(addr('s1'), 2.5), sell(addr('s2'), .6)]
        flow = evaluate(rows, NOW)
        self.assertEqual(flow.reason, 'weak net inflow')   # 5 in, 3.1 out
        rows = [buy(i, sol=1.5) for i in range(10)] + [sell(addr('s'), 9.5)]
        self.assertEqual(evaluate(rows, NOW).reason, 'sell pressure')

    def test_bundled_slot_buys(self):
        rows = [buy(i, slot=77) for i in range(7)] + [buy(i) for i in range(7, 10)]
        self.assertEqual(evaluate(rows, NOW).reason, 'bundled buys')

    def test_momentum_must_be_current(self):
        old = [buy(i, ago=170 - i * 5) for i in range(10)]
        self.assertEqual(evaluate(old, NOW).reason, 'momentum faded')
        rows = [buy(i, ago=40 + (9 - i) * 2) for i in range(10)]
        self.assertEqual(evaluate(rows, NOW, Rules(max_lag=30)).reason, 'stale flow')

    def test_one_instant_burst_is_too_short(self):
        rows = [buy(i, ago=10 + i * .5) for i in range(10)]
        self.assertEqual(evaluate(rows, NOW).reason, 'too short')

    def test_overextended_price(self):
        rows = [buy(i, price=1e-8 * (1 + i * .3)) for i in range(10)]
        self.assertEqual(evaluate(rows, NOW).reason, 'overextended')
        rows = [buy(i, price=1e-8 * (1 + i * .05)) for i in range(10)]
        self.assertTrue(evaluate(rows, NOW).ok)

    def test_unknown_sizes_fail_closed(self):
        rows = [Trade(addr(i), 'buy', 1, None, NOW - 100 + i * 9) for i in range(10)]
        self.assertEqual(evaluate(rows, NOW).reason, 'trade sizes unknown')


if __name__ == '__main__':
    unittest.main()
