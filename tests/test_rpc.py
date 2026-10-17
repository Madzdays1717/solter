import tempfile
import unittest
from pathlib import Path
from solter.config import Config
from solter.service import BoundedRPC,NoBudget,Solter
from solter.storage import Store

class FakeResponse:
    status=200
    async def __aenter__(self):return self
    async def __aexit__(self,*args):return None
    async def json(self,**kw):return {'jsonrpc':'2.0','id':1,'result':{'ok':True}}

class FakeSession:
    def __init__(self):self.attempts=0
    def post(self,*args,**kwargs):self.attempts+=1;return FakeResponse()

class RPCTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Store(Path(self.tmp.name)/'budget.sqlite3')
        self.session=FakeSession()
        self.cfg=Config('t','c','https://example.com','wss://example.com',
            'https://api.dexscreener.com',3,240,20,1,2.0,
            Path(self.tmp.name)/'db.sqlite3',5,5,6,35,True)
    async def asyncTearDown(self):self.db.db.close();self.tmp.cleanup()

    async def test_get_transaction_supports_version_1(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        hunter=Solter(self.cfg,self.db)
        self.db.watching=lambda: [{'mint':'MINT','curve':'CURVE','stage':'new'}]
        fake_rpc=SimpleNamespace(call=AsyncMock(return_value=None))
        hunter.rpc=fake_rpc
        hunter.queue.put_nowait(('MINT','SIGNATURE'))
        worker=asyncio.create_task(hunter.transactions())
        try:
            await asyncio.wait_for(hunter.queue.join(),timeout=2)
            fake_rpc.call.assert_awaited_once_with('getTransaction',[
                'SIGNATURE', {'encoding':'jsonParsed','commitment':'confirmed',
                              'maxSupportedTransactionVersion':1}])
        finally:
            worker.cancel()
            await asyncio.gather(worker,return_exceptions=True)

    async def test_request_budget_is_hard_limit(self):
        rpc=BoundedRPC(self.cfg,self.db,self.session)
        result=await rpc.call('getAccountInfo',['a'])
        self.assertEqual(result,{'ok':True})
        self.assertEqual(self.db.budget_used(),1)
        with self.assertRaises(NoBudget):await rpc.call('getAccountInfo',['a'])
        self.assertEqual(self.session.attempts,1)

if __name__=='__main__':unittest.main()
