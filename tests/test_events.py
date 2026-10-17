import time
import unittest

from helpers import CREATOR, MINT, addr, amm_event, cpi_tx, logs, pump_event
from solter.chain import PUMP_AMM, PUMP_PROGRAM, WSOL
from solter.events import (decode_amm_trade, decode_pump_trade, log_payloads, trades_from_logs,
                           trades_from_tx)

CURVE = addr('curve')
POOL = addr('pool')


class EventTests(unittest.TestCase):
    def test_pump_trade_from_logs_needs_no_rpc(self):
        buyer = addr('buyer')
        trades, need_tx = trades_from_logs(logs([pump_event(buyer, 1.25)]), MINT, CURVE, 'new', slot=9)
        self.assertFalse(need_tx)
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual((t.wallet, t.side, t.slot, t.src), (buyer, 'buy', 9, 'log'))
        self.assertAlmostEqual(t.sol, 1.25)
        self.assertAlmostEqual(t.price, 5e-8)
        self.assertGreater(t.progress, 0)

    def test_event_fields_follow_idl_order(self):
        ev = decode_pump_trade(pump_event(addr('u'), 2, buy=False))
        self.assertEqual(ev['mint'], MINT)
        self.assertFalse(ev['is_buy'])
        self.assertEqual(ev['creator'], CREATOR)
        self.assertEqual(ev['ix_name'], 'sell')
        self.assertEqual(ev['quote_mint'], WSOL)
        short = decode_pump_trade(pump_event(addr('u'), 2, full=False))
        self.assertEqual(short['sol'], 2 * 10**9)
        self.assertNotIn('quote_mint', short)

    def test_multiple_trades_in_one_transaction(self):
        payloads = [pump_event(addr(i), .5) for i in range(3)]
        trades, _ = trades_from_logs(logs(payloads), MINT, CURVE, 'new')
        self.assertEqual([t.idx for t in trades], [0, 1, 2])

    def test_other_mint_and_non_sol_quote_ignored(self):
        other = pump_event(addr('u'), 1, mint=addr('other-mint'))
        usdc = pump_event(addr('u'), 1, quote=addr('usdc'))
        trades, need_tx = trades_from_logs(logs([other, usdc]), MINT, CURVE, 'new')
        self.assertEqual(trades, [])
        self.assertTrue(need_tx)   # a Buy ran but nothing usable decoded: fetch it

    def test_spoofed_program_data_is_ignored(self):
        fake = pump_event(addr('attacker'), 99)
        lines = [f'Program {addr("evil")} invoke [1]',
                 f'Program log: Program {PUMP_PROGRAM} invoke [2]',   # text, not a real invoke
                 'Program data: ' + __import__('base64').b64encode(fake).decode(),
                 f'Program {addr("evil")} success']
        payloads, _, trade_ix = log_payloads(lines, PUMP_PROGRAM)
        self.assertEqual(payloads, [])
        self.assertFalse(trade_ix)
        trades, need_tx = trades_from_logs(lines, MINT, CURVE, 'new')
        self.assertEqual((trades, need_tx), ([], False))

    def test_truncated_logs_ask_for_the_transaction(self):
        lines = [f'Program {PUMP_PROGRAM} invoke [1]', 'Log truncated']
        self.assertEqual(trades_from_logs(lines, MINT, CURVE, 'new'), ([], True))

    def test_non_trade_activity_needs_nothing(self):
        lines = [f'Program {PUMP_PROGRAM} invoke [1]', 'Program log: Instruction: CollectCreatorFee',
                 f'Program {PUMP_PROGRAM} success']
        self.assertEqual(trades_from_logs(lines, MINT, CURVE, 'new'), ([], False))

    def test_inner_instruction_events(self):
        tx = cpi_tx([pump_event(addr('cpi-buyer'), .7)])
        trades = trades_from_tx(tx, MINT, CURVE, 'new')
        self.assertEqual(len(trades), 1)
        self.assertEqual((trades[0].src, trades[0].slot), ('cpi', 123))
        tx['meta']['err'] = {'Custom': 1}
        self.assertEqual(trades_from_tx(tx, MINT, CURVE, 'new'), [])

    def test_inner_instruction_from_other_program_ignored(self):
        tx = cpi_tx([pump_event(addr('x'), .7)], program=addr('evil'))
        tx['meta']['logMessages'] = []
        self.assertEqual(trades_from_tx(tx, MINT, CURVE, 'new'), [])

    def test_pumpswap_events(self):
        buyer, seller = addr('amm-buyer'), addr('amm-seller')
        payloads = [amm_event(POOL, buyer, 'buy', sol=2), amm_event(POOL, seller, 'sell', sol=.5),
                    amm_event(addr('other-pool'), buyer, 'buy')]
        trades, _ = trades_from_logs(logs(payloads, PUMP_AMM), MINT, POOL, 'migrated')
        self.assertEqual([(t.wallet, t.side) for t in trades], [(buyer, 'buy'), (seller, 'sell')])
        self.assertAlmostEqual(trades[0].sol, 2)
        self.assertAlmostEqual(trades[0].price, (85 / 200_000_000))
        virtual = decode_amm_trade(amm_event(POOL, buyer, virtual_quote=5))
        self.assertEqual(virtual['virtual_quote'], 5)
        trades, _ = trades_from_logs(logs([amm_event(POOL, buyer, virtual_quote=5)], PUMP_AMM),
                                     MINT, POOL, 'migrated')
        self.assertIsNone(trades[0].price)   # priced elsewhere when virtual reserves apply

    def test_balance_fallback_estimates_sol(self):
        wallet = addr('legacy')
        tx = {'blockTime': time.time(), 'slot': 5, 'meta': {
            'err': None, 'fee': 5000, 'logMessages': [f'Program {PUMP_PROGRAM} invoke [1]'],
            'preBalances': [2_000_000_000], 'postBalances': [1_499_995_000],
            'preTokenBalances': [{'mint': MINT, 'owner': wallet, 'uiTokenAmount': {'amount': '10'}}],
            'postTokenBalances': [{'mint': MINT, 'owner': wallet, 'uiTokenAmount': {'amount': '20000010'}}]},
            'transaction': {'message': {'accountKeys': [{'pubkey': wallet, 'signer': True}]}}}
        trades = trades_from_tx(tx, MINT, CURVE, 'new')
        self.assertEqual((trades[0].side, trades[0].src), ('buy', 'balance'))
        self.assertAlmostEqual(trades[0].sol, .5)
        self.assertAlmostEqual(trades[0].tokens, 20)
        tx['transaction']['message']['accountKeys'].append({'pubkey': CURVE, 'signer': True})
        tx['meta']['postTokenBalances'].append({'mint': MINT, 'owner': addr('second'),
                                                'uiTokenAmount': {'amount': '5'}})
        tx['transaction']['message']['accountKeys'].append({'pubkey': addr('second'), 'signer': True})
        self.assertEqual(trades_from_tx(tx, MINT, CURVE, 'new'), [])   # two signers changed: ambiguous


if __name__ == '__main__':
    unittest.main()
