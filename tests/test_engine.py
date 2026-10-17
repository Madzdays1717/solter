import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import aiohttp

from helpers import CREATOR, MINT, account, addr, amm_event, curve_bytes, logs, pool_account, pump_event
from solter.chain import PUMP_AMM, TOKEN_PROGRAM, associated_account, bonding_curve, migration_pool
from solter.config import Config
from solter.engine import Solter
from solter.storage import Store

CURVE = bonding_curve(MINT)
VAULT = associated_account(CURVE, MINT, TOKEN_PROGRAM)


class FakeRPC:
    def __init__(self, handlers):
        self.handlers = handlers
        self.calls = []

    async def call(self, method, params, kind='decision'):
        self.calls.append((method, params, kind))
        handler = self.handlers.get(method)
        return handler(params) if callable(handler) else handler


class FakeTelegram:
    def __init__(self, state='sent'):
        self.state = state
        self.sent = []
        self.replies = []

    async def call(self, text, mint, image):
        self.sent.append((text, mint, image))
        return self.state, 42 if self.state == 'sent' else None

    async def reply(self, text, message_id):
        self.replies.append((text, message_id))
        return 'sent'


def mint_account():
    return {'owner': TOKEN_PROGRAM, 'data': {'parsed': {'type': 'mint', 'info': {
        'mintAuthority': None, 'freezeAuthority': None, 'supply': str(10**15)}}}}


def holders(top=2.0):
    rows = [{'address': VAULT, 'amount': str(700 * 10**12)}]
    rows += [{'address': addr(f'h{i}'), 'amount': str(int(top * 10**13) - i * 10**12)} for i in range(12)]
    return {'value': rows}


def rpc_for(curve=None, largest=None, dev_amount=10**12):
    curve = curve or curve_bytes(rsol=8 * 10**9, vsol=38 * 10**9)

    def info(params):
        address, options = params
        if address == MINT:
            return {'value': mint_account()}
        if options['encoding'] == 'base64':
            return {'value': account(curve)}
        return {'value': None}
    return FakeRPC({
        'getAccountInfo': info,
        'getTokenLargestAccounts': largest or holders(),
        'getTokenAccountsByOwner': {'value': [{'account': {'data': {'parsed': {'info': {
            'tokenAmount': {'amount': str(dev_amount)}}}}}}]},
        'getMultipleAccounts': lambda params: {'value': [account(curve) for _ in params[0]]},
        'getSignaturesForAddress': [],
    })


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Store(Path(self.tmp.name) / 's.sqlite3')
        self.cfg = Config(tg_token='t', tg_chat='c', db_path=Path(self.tmp.name) / 's.sqlite3')
        self.bot = Solter(self.cfg, self.db)
        self.bot.rpc = rpc_for()
        self.bot.tg = FakeTelegram()
        self.bot.sol_usd = 150.0

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def launch(self, ago=120):
        self.bot.on_portal(json.dumps({'txType': 'create', 'mint': MINT, 'name': 'Coin <b>', 'symbol': 'CN',
                                       'traderPublicKey': CREATOR, 'solAmount': 1.0,
                                       'initialBuy': 30_000_000, 'uri': None}), time.time() - ago)

    def watched(self):
        self.launch()
        asyncio.run(self.bot.sweep_once(time.time()))
        self.bot.schedule(time.time())
        return self.db.token(MINT)

    def buys(self, n=10, sol=.6):
        now = time.time()
        for i in range(n):
            payload = pump_event(addr(f'buyer{i}'), sol, ts=now - 81 + i * 9,
                                 vsol=(38 + i) * 10**9, vtok=int(850e12 - i * 1e12))
            self.bot.on_logs(MINT, CURVE, 'new', f'sig{i}', logs([payload]), 1000 + i)

    def test_launch_to_watch(self):
        token = self.watched()
        self.assertEqual((token['state'], token['account']), ('watching', CURVE))
        self.assertGreater(token['traction'], 0)
        self.assertEqual(self.db.metrics()['promoted'], 1)
        multiple = [c for c in self.bot.rpc.calls if c[0] == 'getMultipleAccounts']
        self.assertEqual(multiple[0][1][0], [CURVE])

    def test_trades_from_logs_cost_no_rpc(self):
        self.watched()
        before = len(self.bot.rpc.calls)
        self.buys()
        self.assertEqual(len(self.db.trades(MINT)), 10)
        self.assertEqual(len(self.bot.rpc.calls), before)
        self.assertEqual(self.db.metrics()['trade_from_logs'], 10)
        self.bot.on_logs(MINT, CURVE, 'new', 'sig0', logs([pump_event(addr('x'), 1)]), 1)
        self.assertEqual(len(self.db.trades(MINT)), 10)          # a signature is processed once

    def test_undecodable_trade_goes_to_the_fallback_queue(self):
        self.watched()
        self.bot.on_logs(MINT, CURVE, 'new', 'opaque', [f'Program {addr("x")} invoke [1]', 'Log truncated'], 1)
        self.assertEqual(self.bot.fallback.qsize(), 1)

    def test_good_flow_becomes_one_call_with_outcome_tracking(self):
        self.watched()
        self.buys()
        asyncio.run(self.bot.consider(self.db.token(MINT), time.time()))
        self.assertEqual(len(self.bot.tg.sent), 1, self.db.token(MINT)['reason'])
        text = self.bot.tg.sent[0][0]
        self.assertIn('SOLTER CALL', text)
        self.assertIn('Coin &lt;b&gt;', text)
        self.assertIn(MINT, text)
        call = self.db.call(MINT)
        self.assertEqual((call['state'], call['message_id']), ('sent', 42))
        self.assertEqual(self.db.token(MINT)['state'], 'called')
        self.assertIsNotNone(self.db.outcome(MINT))
        asyncio.run(self.bot.consider(self.db.token(MINT), time.time()))
        self.assertEqual(len(self.bot.tg.sent), 1)

    def test_concentrated_holders_block_the_call(self):
        self.bot.rpc = rpc_for(largest=holders(top=6.0))
        self.watched()
        self.buys()
        asyncio.run(self.bot.consider(self.db.token(MINT), time.time()))
        self.assertEqual(self.bot.tg.sent, [])
        self.assertEqual(self.db.token(MINT)['reason'], 'top holders concentrated')
        self.assertEqual(self.db.metrics()['no_call:top holders concentrated'], 1)

    def test_curve_creator_overrides_and_its_sale_blocks(self):
        self.bot.rpc = rpc_for(curve=curve_bytes(rsol=8 * 10**9, vsol=38 * 10**9, creator=addr('real-dev')))
        self.watched()
        self.buys()
        sale = pump_event(addr('real-dev'), .2, buy=False, ts=time.time() - 50)
        self.bot.on_logs(MINT, CURVE, 'new', 'devsale', logs([sale], ix='Sell'), 2000)
        asyncio.run(self.bot.consider(self.db.token(MINT), time.time()))
        self.assertEqual(self.bot.tg.sent, [])
        self.assertEqual(self.db.token(MINT)['creator'], addr('real-dev'))

    def test_follow_ups_after_the_call(self):
        self.watched()
        self.buys()
        asyncio.run(self.bot.consider(self.db.token(MINT), time.time()))
        entry = self.db.outcome(MINT)['entry']
        later = time.time() + 1
        pump = pump_event(addr('late'), 3, ts=later, vsol=int(38e9 * 2), vtok=int(850e12 / 2))
        self.bot.on_logs(MINT, CURVE, 'new', 'moon', logs([pump]), 3000)
        sale = pump_event(CREATOR, .1, buy=False, ts=later)
        self.bot.on_logs(MINT, CURVE, 'new', 'dev', logs([sale], ix='Sell'), 3001)
        o = self.db.outcome(MINT)
        self.assertGreaterEqual(o['peak'] / entry, 3)
        self.assertEqual(o['dev_sold'], 1)
        outcome = self.db.open_outcomes()[0]
        asyncio.run(self.bot.follow(outcome, outcome['call_at'] + 16 * 60))
        texts = [r[0] for r in self.bot.tg.replies]
        self.assertTrue(any('3.' in t and 'sejak call' in t for t in texts), texts)
        self.assertTrue(any('Dev' in t for t in texts))
        o = self.db.outcome(MINT)
        self.assertEqual(o['milestone'], 3)
        self.assertIsNotNone(o['r5'])
        self.assertIsNotNone(o['r15'])
        self.assertIsNone(o['r30'])
        asyncio.run(self.bot.follow(self.db.open_outcomes()[0], outcome['call_at'] + 61 * 60))
        self.assertEqual(self.db.outcome(MINT)['done'], 1)
        self.assertEqual(self.db.subscriptions(), [])

    def test_migration_points_to_the_derived_pool(self):
        self.launch()
        self.bot.on_portal(json.dumps({'txType': 'migrate', 'mint': MINT, 'pool': 'pump-amm'}), time.time())
        token = self.db.token(MINT)
        self.assertEqual((token['stage'], token['account'], token['state']),
                         ('migrated', migration_pool(MINT), 'alive'))
        self.bot.schedule(time.time())
        self.assertEqual(self.db.token(MINT)['state'], 'watching')
        pool = migration_pool(MINT)
        trade = amm_event(pool, addr('amm'), 'buy', sol=1.5)
        self.bot.on_logs(MINT, pool, 'migrated', 'amm1', logs([trade], PUMP_AMM), 5)
        self.assertEqual(len(self.db.trades(MINT)), 1)

    def test_migrated_pool_is_verified_before_a_call(self):
        self.launch()
        self.bot.on_portal(json.dumps({'txType': 'migrate', 'mint': MINT}), time.time())
        pool = migration_pool(MINT)

        def info(params):
            address = params[0]
            if address == MINT:
                return {'value': mint_account()}
            return {'value': pool_account(base_mint=addr('other'))}
        self.bot.rpc = FakeRPC({'getAccountInfo': info})

        async def no_pair(mint, max_age=30):
            return {}
        self.bot.pair = no_pair
        result = asyncio.run(self.bot.deep_check(self.db.token(MINT), []))
        self.assertEqual(result.reason, 'pool unverified')
        self.assertEqual(self.db.token(MINT)['account'], pool)

    def test_sweep_interval_respects_the_budget(self):
        self.assertEqual(self.bot.sweep_interval(1), 30)
        small = Solter(Config(rpc_per_day=1800), self.db)
        self.assertAlmostEqual(small.sweep_interval(1), 86400 / (1800 * .45))
        self.assertAlmostEqual(small.reservoir.stale_after, 3 * small.sweep_interval(2))
        self.assertEqual(self.bot.reservoir.stale_after, 300)

    def test_device_clock_behind_the_chain_is_corrected(self):
        now = time.time()
        for i in range(12):
            payload = pump_event(addr(f'c{i}'), .5, ts=now + 30 - (i % 3))   # phone 30 s slow
            self.bot.on_logs(MINT, CURVE, 'new', f'clk{i}', logs([payload]), i)
        self.assertAlmostEqual(self.bot.clock_offset(), 30, delta=1.5)
        self.assertAlmostEqual(self.bot.chain_now(now), now + 30, delta=1.5)
        self.assertGreater(self.bot.clock_warned, 0)
        self.bot.lags.clear()
        self.bot.lags.extend([-2.0, -1.0, -3.0] * 5)    # ordinary latency: no correction
        self.assertEqual(self.bot.clock_offset(), 0.0)

    def test_metadata_failure_is_retried_later(self):
        token = {'mint': MINT, 'uri': 'https://ipfs.io/ipfs/Qm'}
        answers = [None, {'twitter': 'https://x.com/a'}]

        async def fetch(url, limit=0):
            return answers.pop(0)
        self.bot.http_json = fetch
        self.assertIsNone(asyncio.run(self.bot.token_metadata(token)))
        self.assertIsNone(asyncio.run(self.bot.token_metadata(token)))          # cached for a while
        self.bot.metadata[MINT] = (time.time() - 400, None)
        self.assertEqual(asyncio.run(self.bot.token_metadata(token)), {'twitter': 'https://x.com/a'})

    def test_http_json_reads_a_body_sent_in_parts(self):
        from aiohttp import web
        big = json.dumps([{'pair': i, 'pad': 'x' * 100} for i in range(160)]).encode()

        async def handler(request):
            response = web.StreamResponse(headers={'Content-Type': 'application/json'})
            await response.prepare(request)
            await response.write(big[:len(big) // 2])
            await asyncio.sleep(.05)
            await response.write(big[len(big) // 2:])
            await response.write_eof()
            return response

        async def run():
            app = web.Application()
            app.router.add_get('/pairs', handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            port = runner.addresses[0][1]
            try:
                async with aiohttp.ClientSession() as session:
                    self.bot.session = session
                    return await self.bot.http_json(f'http://127.0.0.1:{port}/pairs')
            finally:
                await runner.cleanup()
        result = asyncio.run(run())
        self.assertEqual(len(result), 160)

    def test_logs_websocket_routes_notifications(self):
        self.watched()
        payload = pump_event(addr('ws-buyer'), .9)
        messages = [{'jsonrpc': '2.0', 'id': 1, 'result': 77},
                    {'jsonrpc': '2.0', 'method': 'logsNotification', 'params': {
                        'subscription': 77, 'result': {'context': {'slot': 555}, 'value': {
                            'signature': 'wsig', 'err': None, 'logs': logs([payload])}}}}]

        class FakeWS:
            def __init__(self):
                self.sent = []

            async def send_json(self, obj):
                self.sent.append(obj)

            async def receive(self):
                if messages:
                    return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(messages.pop(0)))
                return SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None)

        ws = FakeWS()
        asyncio.run(self.bot.serve_logs(ws))
        self.assertEqual(ws.sent[0]['method'], 'logsSubscribe')
        self.assertEqual(ws.sent[0]['params'][0], {'mentions': [CURVE]})
        trades = self.db.trades(MINT)
        self.assertEqual([(t.wallet, t.slot) for t in trades], [(addr('ws-buyer'), 555)])


if __name__ == '__main__':
    unittest.main()
