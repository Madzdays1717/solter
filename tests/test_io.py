"""RPC budget, Telegram delivery, the report and configuration."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from helpers import MINT, addr
from solter.config import Config
from solter.flow import Flow
from solter.quality import Assessment, Holders, Socials
from solter.report import build, render
from solter.rpc import SWEEP, BoundedRPC, NoBudget
from solter.sources import pair_facts, parse_portal, pumpswap_pair, sol_usd_from
from solter.storage import Store
from solter.telegram import Telegram, call_text


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = body if body is not None else {'jsonrpc': '2.0', 'id': 1, 'result': {'ok': True}}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def json(self, **kw):
        return self.body


class FakeSession:
    def __init__(self, response=None):
        self.attempts = 0
        self.response = response or FakeResponse()
        self.posts = []

    def post(self, url, **kwargs):
        self.attempts += 1
        self.posts.append((url, kwargs))
        return self.response


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Store(Path(self.tmp.name) / 's.sqlite3')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()


class RPCTests(StoreCase):
    def test_budget_shares_and_hard_limit(self):
        session = FakeSession()
        rpc = BoundedRPC('https://rpc', self.db, 100, 10, session)

        async def run():
            for _ in range(6):
                self.assertEqual(await rpc.call('getAccountInfo', ['a'], share=SWEEP), {'ok': True})
            with self.assertRaises(NoBudget):
                await rpc.call('getMultipleAccounts', [[]], share=SWEEP)   # sweep may use 60%
            for _ in range(4):
                await rpc.call('getAccountInfo', ['a'])                     # decisions use the rest
            with self.assertRaises(NoBudget):
                await rpc.call('getAccountInfo', ['a'])
        asyncio.run(run())
        self.assertEqual(session.attempts, 10)
        self.assertEqual(self.db.budget_used(), 10)

    def test_rpc_error_is_none(self):
        session = FakeSession(FakeResponse(body={'error': {'code': -32015}}))
        rpc = BoundedRPC('https://rpc', self.db, 100, 10, session)
        self.assertIsNone(asyncio.run(rpc.call('getTransaction', ['s'])))


class TelegramTests(unittest.TestCase):
    def run_call(self, results, text='hello', image='https://img/x.png'):
        sent = []
        tg = Telegram('token', 'chat', None)

        async def send(method, payload):
            sent.append((method, payload))
            return results.get(method, ('sent', 7))
        tg.send = send
        return asyncio.run(tg.call(text, MINT, image)), sent

    def test_photo_with_caption_once(self):
        (state, mid), sent = self.run_call({})
        self.assertEqual((state, mid, [m for m, _ in sent]), ('sent', 7, ['sendPhoto']))

    def test_rejected_photo_falls_back_to_text_once(self):
        (state, _), sent = self.run_call({'sendPhoto': ('rejected', None)})
        self.assertEqual([m for m, _ in sent], ['sendPhoto', 'sendMessage'])

    def test_uncertain_is_never_retried(self):
        (state, _), sent = self.run_call({'sendPhoto': ('uncertain', None)})
        self.assertEqual((state, [m for m, _ in sent]), ('uncertain', ['sendPhoto']))

    def test_long_text_skips_the_caption(self):
        (_, _), sent = self.run_call({}, text='x' * 1200)
        self.assertEqual([m for m, _ in sent], ['sendMessage'])

    def test_call_text_escapes_and_reports(self):
        token = {'mint': MINT, 'symbol': '<b>X', 'name': 'A&B', 'stage': 'new', 'created_at': time.time() - 400}
        flow = Flow(True, 'ok', 12, 2, 11, 5, 6, 7.5, 1.2, .2, 0, .35)
        a = Assessment(None, 6e-8, 60, 33, Holders(18.5, 4.1, 2), Socials('https://x.com/a"b'), 1, 3.0, 82)
        text = call_text(token, flow, a, 150, time.time(), 180)
        self.assertIn('&lt;b&gt;X', text)
        self.assertIn('A&amp;B', text)
        self.assertIn('Grade <b>A</b> · 82/100', text)
        self.assertIn('$9.0K', text)
        self.assertIn('+6.3 SOL', text)
        self.assertIn('Top10 18.5%', text)
        self.assertIn('launch ke-1', text)
        self.assertIn('href="https://x.com/a&quot;b"', text)


class SourceTests(unittest.TestCase):
    def test_portal_messages(self):
        kind, info = parse_portal('{"txType":"create","mint":"%s","name":"N","symbol":"S",'
                                  '"traderPublicKey":"%s","solAmount":1.5,"initialBuy":35000000,'
                                  '"uri":"https://ipfs.io/ipfs/Qm"}' % (MINT, addr('c')))
        self.assertEqual((kind, info['creator'], info['dev_sol'], info['dev_tokens']),
                         ('create', addr('c'), 1.5, 35e6))
        self.assertEqual(parse_portal('{"txType":"migrate","mint":"%s"}' % MINT), ('migrate', {'mint': MINT}))
        self.assertEqual(parse_portal('{"mint":"%s","pool":"pump-amm"}' % MINT)[0], 'migrate')
        self.assertIsNone(parse_portal('{"message":"Successfully subscribed"}'))
        self.assertIsNone(parse_portal('not json'))
        _, info = parse_portal('{"txType":"create","mint":"%s","uri":"javascript:x"}' % MINT)
        self.assertIsNone(info['uri'])

    def test_dexscreener_parsing(self):
        wsol = 'So11111111111111111111111111111111111111112'
        pairs = [{'chainId': 'solana', 'baseToken': {'address': wsol}, 'quoteToken': {'symbol': 'USDC'},
                  'priceUsd': '151.2', 'liquidity': {'usd': 9e6}},
                 {'chainId': 'solana', 'baseToken': {'address': wsol}, 'quoteToken': {'symbol': 'USDT'},
                  'priceUsd': '149', 'liquidity': {'usd': 1e6}},
                 {'chainId': 'solana', 'baseToken': {'address': wsol}, 'quoteToken': {'symbol': 'BONK'},
                  'priceUsd': '999', 'liquidity': {'usd': 9e9}}]
        self.assertEqual(sol_usd_from(pairs), 151.2)
        self.assertIsNone(sol_usd_from({'error': 1}))
        pair = pumpswap_pair([{'chainId': 'solana', 'baseToken': {'address': MINT, 'symbol': 'S'},
                               'dexId': 'pumpswap', 'pairAddress': addr('p'), 'priceNative': '0.0000001',
                               'liquidity': {'usd': 20000}}], MINT)
        facts = pair_facts(pair)
        self.assertEqual((facts['address'], facts['price_sol'], facts['liquidity_usd']), (addr('p'), 1e-7, 20000))


class ReportTests(StoreCase):
    def test_report_numbers(self):
        now = time.time()
        for i, (peak, grade) in enumerate(((3.0, 'A'), (1.2, 'B'), (0.9, 'C'))):
            mint = addr(f'r{i}')
            self.db.claim_call(mint, 'new', 10, now - 3600)
            self.db.finish_call(mint, 'sent', i, {'grade': grade, 'score': 80 - i * 10, 'symbol': f'S{i}'})
            self.db.open_outcome(mint, 1.0, now - 3600)
            self.db.track(mint, peak, now - 3000)
            self.db.track(mint, .5, now - 2000)
            self.db.set_outcome(mint, r15=peak, r60=.5, done=1)
        self.db.metric('no_call:few buyers', 5)
        rep = build(self.db, now, 7)
        self.assertEqual((rep['calls'], rep['tracked'], rep['finished']), (3, 3, 3))
        self.assertAlmostEqual(rep['peak_hit'][2], 1 / 3)
        self.assertAlmostEqual(rep['median_peak'], 1.2)
        self.assertAlmostEqual(rep['median_return'][60], -.5)
        self.assertEqual(rep['by_grade']['A']['hit_2x'], 1.0)
        text = render(rep)
        self.assertIn('≥2x 33%', text)
        self.assertIn('few buyers: 5', text)
        self.assertIn('Belum ada call', render(build(Store(Path(self.tmp.name) / 'empty.sqlite3'), now)))


class ConfigTests(unittest.TestCase):
    def test_defaults_and_v1_names(self):
        cfg = Config.load({'TELEGRAM_BOT_TOKEN': 'secret', 'TELEGRAM_CHAT_ID': '1',
                           'MAX_ACTIVE_TOKENS': '5', 'MIN_BUYS': '6', 'REQUIRE_SOCIALS': 'yes',
                           'SOLANA_RPC_URL': 'https://rpc.example/?api-key=SECRET'})
        self.assertEqual((cfg.max_watch, cfg.min_buys, cfg.require_socials), (5, 6, True))
        self.assertEqual(cfg.rpc_ws, 'wss://rpc.example/?api-key=SECRET')
        public = cfg.public()
        self.assertNotIn('SECRET', str(public))
        self.assertNotIn('secret', str(public))
        self.assertEqual(cfg.rules().max_wallet_share, .30)

    def test_validation(self):
        with self.assertRaises(ValueError):
            Config.load({'MAX_ACTIVE_TOKENS': '99'})
        with self.assertRaises(ValueError):
            Config.load({'MIN_NET_SOL': 'lots'})
        with self.assertRaises(ValueError):
            Config.load({'SOLANA_RPC_URL': 'http://insecure'})


if __name__ == '__main__':
    unittest.main()
