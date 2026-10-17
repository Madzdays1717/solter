import unittest

from helpers import CREATOR, MINT, account, curve_bytes, pool_account
from solter.chain import (PUMP_PROGRAM, SYSTEM, TOKEN_2022, TOKEN_PROGRAM, WSOL, associated_account,
                          bonding_curve, curve_account, decode58, encode58, find_pda, migration_pool,
                          mint_safety, mint_supply, on_curve, pool_account as decode_pool, valid_address)


class ChainTests(unittest.TestCase):
    def test_base58_round_trip(self):
        self.assertTrue(valid_address(MINT))
        self.assertFalse(valid_address('fake.address'))
        self.assertFalse(valid_address(None))
        self.assertEqual(encode58(decode58(MINT)), MINT)
        self.assertEqual(encode58(b'\x00' * 32), SYSTEM)

    def test_pda_matches_known_pump_addresses(self):
        # Pump's Global account and event authority, as seen on mainnet.
        self.assertEqual(find_pda([b'global'], PUMP_PROGRAM), '4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf')
        self.assertEqual(find_pda([b'__event_authority'], PUMP_PROGRAM),
                         'Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1')

    def test_derived_accounts_are_off_curve_and_stable(self):
        for address in (bonding_curve(MINT), migration_pool(MINT), associated_account(CREATOR, MINT)):
            self.assertTrue(valid_address(address))
            self.assertFalse(on_curve(decode58(address)))
        self.assertEqual(migration_pool(MINT), migration_pool(MINT))
        self.assertNotEqual(associated_account(CREATOR, MINT, TOKEN_PROGRAM),
                            associated_account(CREATOR, MINT, TOKEN_2022))

    def test_mint_safety(self):
        safe = {'owner': TOKEN_PROGRAM, 'data': {'parsed': {'type': 'mint', 'info': {
            'mintAuthority': None, 'freezeAuthority': None, 'supply': '1000000000000000'}}}}
        self.assertTrue(mint_safety(safe)[0])
        self.assertEqual(mint_supply(safe), 10**15)
        self.assertFalse(mint_safety(None)[0])
        safe['data']['parsed']['info']['freezeAuthority'] = 'x'
        self.assertFalse(mint_safety(safe)[0])
        safe['data']['parsed']['info']['freezeAuthority'] = None
        safe['owner'] = TOKEN_2022
        safe['data']['parsed']['info']['extensions'] = [{'extension': 'TransferFeeConfig'}]
        self.assertFalse(mint_safety(safe)[0])
        safe['data']['parsed']['info']['extensions'] = [{'extension': 'metadataPointer'}]
        self.assertTrue(mint_safety(safe)[0])

    def test_curve_price_mcap_progress(self):
        curve = curve_account(account(curve_bytes()))
        self.assertEqual(curve.creator, CREATOR)
        self.assertTrue(curve.sol_quoted)
        # 40 SOL / 800M tokens = 5e-8 SOL per token; 1B supply -> 50 SOL market cap.
        self.assertAlmostEqual(curve.price, 5e-8)
        self.assertAlmostEqual(curve.mcap_sol, 50)
        self.assertAlmostEqual(curve.progress, 1 - 600 / 793.1, places=6)
        self.assertIsNone(curve_account(account(curve_bytes(), owner='bad')))
        self.assertTrue(curve_account(account(curve_bytes(mayhem=True))).mayhem)
        self.assertEqual(curve_account(account(curve_bytes(complete=True))).progress, 1.0)
        self.assertTrue(curve_account(account(curve_bytes(quote=SYSTEM))).sol_quoted)
        self.assertFalse(curve_account(account(curve_bytes(quote=MINT))).sol_quoted)
        self.assertIsNone(curve_account(account(curve_bytes()[:100])))

    def test_pool_decoding(self):
        pool = decode_pool(pool_account())
        self.assertEqual(pool.base_mint, MINT)
        self.assertEqual(pool.quote_mint, WSOL)
        self.assertEqual(pool.coin_creator, CREATOR)
        self.assertIsNone(decode_pool(account(b'\x00' * 300)))


if __name__ == '__main__':
    unittest.main()
