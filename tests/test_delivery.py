import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from dataclasses import replace
from solter.config import Config
from solter.flow import Signal
from solter.service import Solter
from solter.storage import Store

M='SguaCwgH7g45ZCc7SB19NadCc2XjsPDo12DMjvfpump'

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Store(Path(self.tmp.name)/'s.sqlite3')
        self.cfg=Config('token','chat','https://example.com','wss://example.com',
                        'https://api.dexscreener.com',3,240,10,1000,4.,Path(self.tmp.name)/'s.sqlite3',
                        5,5,6,35,True)
        self.db.admit(M,{'symbol':'ABC','name':'ABC coin'},5,240)
        self.t=self.db.watching()[0]
        self.signal=Signal(True,'ok',7,1,7,4,4)
    def tearDown(self):self.db.db.close();self.tmp.cleanup()

    def test_photo_only_one_message(self):
        h=Solter(self.cfg,self.db)
        sent=[]
        async def tg(method,payload):sent.append((method,payload));return 'sent'
        h.telegram=tg
        asyncio.run(h.send_call(self.t,self.signal,0.00001,'https://img.example/photo.png',0.0))
        self.assertEqual([x[0] for x in sent],['sendPhoto'])
        self.assertIn('caption',sent[0][1]);self.assertTrue(self.db.has_call(M))
        asyncio.run(h.send_call(self.t,self.signal,0.00001,None,0.0))
        self.assertEqual(len(sent),1)

    def test_photo_bad_falls_back_once(self):
        h=Solter(self.cfg,self.db);sent=[]
        async def tg(method,payload):sent.append(method);return 'rejected' if method=='sendPhoto' else 'sent'
        h.telegram=tg
        asyncio.run(h.send_call(self.t,self.signal,0.01,'https://img.example/nope.png',0.0))
        self.assertEqual(sent,['sendPhoto','sendMessage'])
        self.assertEqual(self.db.stats()['calls'],[('sent',1)])

    def test_photo_timeout_no_duplicate(self):
        h=Solter(self.cfg,self.db);sent=[]
        async def tg(method,payload):sent.append(method);return 'uncertain'
        h.telegram=tg
        asyncio.run(h.send_call(self.t,self.signal,0.01,'https://img.example/nope.png',0.0))
        self.assertEqual(sent,['sendPhoto'])
        self.assertEqual(self.db.stats()['calls'],[('uncertain',1)])

    def test_text_only(self):
        h=Solter(self.cfg,self.db);sent=[]
        async def tg(method,payload):sent.append(method);return 'sent'
        h.telegram=tg
        asyncio.run(h.send_call(self.t,self.signal,0.01,None,0))
        self.assertEqual(sent,['sendMessage'])

if __name__=='__main__':unittest.main()
