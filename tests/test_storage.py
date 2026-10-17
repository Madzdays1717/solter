import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from solter.storage import Store

M='SguaCwgH7g45ZCc7SB19NadCc2XjsPDo12DMjvfpump'

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Store(Path(self.tmp.name)/'s.sqlite3')
        self.event={'symbol':'AAA','name':'Coin'}
    def tearDown(self):self.db.db.close();self.tmp.cleanup()

    def test_budget(self):
        self.assertTrue(self.db.budget_claim(2))
        self.assertTrue(self.db.budget_claim(2))
        self.assertFalse(self.db.budget_claim(2))
        self.assertEqual(self.db.budget_used(),2)

    def test_only_one_call(self):
        self.assertTrue(self.db.claim_call(M,'new',4))
        self.assertFalse(self.db.claim_call(M,'migrated',4))
        self.db.finish_call(M,'sent')
        self.assertTrue(self.db.has_call(M))

    def test_migration_changes_watch_account_and_clears_old_flow(self):
        self.assertTrue(self.db.admit(M,self.event,3,240))
        self.db.set_curve(M,'old_bonding_curve')
        self.db.set_market(M,.001,None)
        self.db.record_trade(M,'tx1','wallet','buy',100,__import__('time').time())
        self.db.migrate(M)
        now=self.db.watching()[0]
        self.assertEqual(now['stage'],'migrated')
        self.assertEqual(now['curve'],'')
        self.assertIsNone(now['first_price'])
        self.assertEqual(self.db.trades(M),[])
        self.db.record_trade(M,'tx2','wallet','buy',100,__import__('time').time())
        self.db.migrate(M)
        self.assertEqual(len(self.db.trades(M)),1)

    def test_token_lifecycle(self):
        self.assertTrue(self.db.admit(M,self.event,1,240))
        self.assertFalse(self.db.admit(M,self.event,1,240))
        self.assertEqual(self.db.watching()[0]['stage'],'new')
        self.db.migrate(M)
        self.assertEqual(self.db.watching()[0]['stage'],'migrated')
        self.assertTrue(self.db.record_trade(M,'sig','wallet','buy',123,100))
        self.assertFalse(self.db.record_trade(M,'sig','wallet','buy',123,100))
        self.db.mark_processed(M,'sig')
        self.assertTrue(self.db.seen_trade(M,'sig'))
        self.db.db.execute("UPDATE tokens SET state='expired' WHERE mint=?",(M,));self.db.db.commit()
        self.assertTrue(self.db.admit(M,self.event,1,240))
        self.assertEqual(self.db.trades(M,10**10),[])

if __name__=='__main__':unittest.main()
