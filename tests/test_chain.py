import base64
import struct
import unittest
from solter.chain import (
    PUMP_PROGRAM,DISC,bonding_pda,curve_metadata,decode58,encode58,
    mint_safety,valid_address,TOKEN_PROGRAM,TOKEN_2022)

MINT='SguaCwgH7g45ZCc7SB19NadCc2XjsPDo12DMjvfpump'

class ChainTests(unittest.TestCase):
    def test_pubkeys(self):
        self.assertTrue(valid_address(MINT))
        self.assertFalse(valid_address('fake.address'))
        self.assertEqual(encode58(decode58(MINT)),MINT)
        self.assertEqual(len(decode58(bonding_pda(MINT))),32)

    def test_mint_safety(self):
        safe={'owner':TOKEN_PROGRAM,'data':{'parsed':{'type':'mint','info':{'mintAuthority':None,'freezeAuthority':None}}}}
        self.assertEqual(mint_safety(safe)[0],True)
        self.assertEqual(mint_safety(None)[0],False)
        safe['data']['parsed']['info']['mintAuthority']='a'
        self.assertFalse(mint_safety(safe)[0])
        safe['data']['parsed']['info']['mintAuthority']=None
        safe['owner']=TOKEN_2022
        safe['data']['parsed']['info']['extensions']=[{'extension':'TransferFeeConfig'}]
        self.assertFalse(mint_safety(safe)[0])

    def test_curve(self):
        raw=bytearray(115);raw[:8]=DISC
        struct.pack_into('<QQQQQ',raw,8,100,100,100,100,1000)
        raw[49:81]=decode58(MINT)
        raw[83:115]=decode58(MINT)
        obj={'owner':PUMP_PROGRAM,'data':[base64.b64encode(raw).decode(),'base64']}
        self.assertEqual(curve_metadata(obj)['creator'],MINT)
        self.assertFalse(curve_metadata(obj)['complete'])
        raw[81]=1;obj['data'][0]=base64.b64encode(raw).decode()
        self.assertTrue(curve_metadata(obj)['mayhem'])
        obj['owner']='bad';self.assertIsNone(curve_metadata(obj))
