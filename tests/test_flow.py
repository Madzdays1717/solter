import time
import unittest
from solter.flow import evaluate,parse_trade
from solter.chain import PUMP_PROGRAM

MINT='SguaCwgH7g45ZCc7SB19NadCc2XjsPDo12DMjvfpump'

class FlowTests(unittest.TestCase):
    def setUp(self):self.now=time.time()

    def buys(self):
        return [(self.now-60+i*10,'wallet'+str(i),'buy',100) for i in range(7)]

    def test_burst_flow(self):
        s=evaluate(self.buys(),self.now)
        self.assertTrue(s.ok,s.reason)
        self.assertEqual(s.buyers,7)

    def test_repeated_wallets_fail(self):
        rows=[(self.now-50+i*8,'same','buy',1) for i in range(7)]
        self.assertFalse(evaluate(rows,self.now).ok)

    def test_sell_count_fail(self):
        rows=self.buys()+[(self.now-10,'seller'+str(i),'sell',50) for i in range(7)]
        self.assertFalse(evaluate(rows,self.now).ok)

    def test_old_flow_fails(self):
        rows=[(self.now-130+i*3,'wallet'+str(i),'buy',3) for i in range(9)]
        self.assertFalse(evaluate(rows,self.now).ok)

    def test_short_burst_fails(self):
        rows=[(self.now-1-i,'wallet'+str(i),'buy',1) for i in range(7)]
        self.assertFalse(evaluate(rows,self.now).ok)

    def test_tx_classifier(self):
        wallet='AWallet'
        tx={'blockTime':self.now,'meta':{'err':None,'logMessages':[f'Program {PUMP_PROGRAM} invoke [1]'],
             'preTokenBalances':[{'mint':MINT,'owner':wallet,'uiTokenAmount':{'amount':'10'}}],
             'postTokenBalances':[{'mint':MINT,'owner':wallet,'uiTokenAmount':{'amount':'20'}}]},
            'transaction':{'message':{'accountKeys':[{'pubkey':wallet,'signer':True}]}}}
        t=parse_trade(tx,MINT,'curve')
        self.assertEqual(t.side,'buy')
        self.assertEqual(t.amount,10)
        tx['meta']['logMessages']=[]
        self.assertIsNone(parse_trade(tx,MINT,'curve'))
        tx['meta']['logMessages']=[f'Program {PUMP_PROGRAM} invoke [1]']
        tx['meta']['postTokenBalances'][0]['uiTokenAmount']['amount']='5'
        self.assertEqual(parse_trade(tx,MINT,'curve').side,'sell')
        tx['meta']['err']={'Custom':1}
        self.assertIsNone(parse_trade(tx,MINT,'curve'))

if __name__=='__main__':unittest.main()
