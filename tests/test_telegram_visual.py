"""Rendered market alert and optional art, including hostile metadata."""
import asyncio
import html
import json
import tempfile
import time
import unittest
from pathlib import Path

from helpers import MINT
from solter.config import Config
from solter.engine import Solter
from solter.market_media import (combine_media, extract_profile_media, public_https)
from solter.storage import Store
from solter.telegram import Telegram, links, market_alert_text, milestone_text, dev_sold_text
from test_market_only import FakeTelegram, dex_pair


class MediaValidationTests(unittest.TestCase):
    def test_banner_preferred_and_socials_guarded(self):
        pair = dex_pair(info={'imageUrl': 'https://cdn.example/token.png',
                              'socials': [{'type': 'twitter', 'url': 'https://x.com/real'},
                                          {'type': 'telegram', 'url': 'javascript:alert(1)'}],
                              'websites': [{'url': 'https://example.org'}]})
        cached = {'header': 'https://cdn.example/banner.jpg',
                  'icon': 'https://cdn.example/icon.png',
                  'socials': {'Telegram': 'https://t.me/real'}}
        media = combine_media(pair, cached)
        self.assertEqual(media['image'], 'https://cdn.example/banner.jpg')
        self.assertEqual(media['image_kind'], 'banner')
        self.assertEqual(media['socials'], {
            'X': 'https://x.com/real', 'Telegram': 'https://t.me/real',
            'Website': 'https://example.org'})
        without_banner = combine_media(pair, None)
        self.assertEqual(without_banner['image'], 'https://cdn.example/token.png')
        self.assertEqual(without_banner['image_kind'], 'icon')
        self.assertIsNone(combine_media(dex_pair(), None)['image'])

    def test_profile_parsing_and_rejecting_unsafe_urls(self):
        item = extract_profile_media({
            'chainId': 'solana', 'tokenAddress': MINT,
            'header': 'https://cdn.example/header.webp',
            'icon': 'http://cdn.example/logo.png',
            'links': [{'type': 'telegram', 'url': 'https://t.me/abc'}]})
        self.assertEqual(item[1]['header'], 'https://cdn.example/header.webp')
        self.assertIsNone(item[1]['icon'])
        self.assertIsNone(extract_profile_media({'chainId': 'ethereum', 'tokenAddress': MINT}))
        for value in ['http://site/image.jpg', 'https://127.0.0.1/secret',
                      'https://localhost/img', 'https://site/img\nheader',
                      'https://user:pass@example.org/x', 'javascript:alert(1)',
                      'https://192.168.1.10/photo', 'https://site.com:8080/photo']:
            self.assertIsNone(public_https(value), value)

    def test_message_clear_short_and_escapes_all_urls(self):
        t = {'mint': MINT, 'name': '<script>&evil', 'symbol': 'X&Y',
             'market_buys': 32, 'market_sells': 8, 'market_volume_usd': 5000,
             'market_liquidity_usd': 30000, 'market_change_pct': 25.4,
             'market_mcap_usd': 19000, 'pair_created_at': time.time() - 7200}
        msg = market_alert_text(t, 77, time.time(), {'X': 'https://x.com/a?x=1&b=2',
                             'Website': 'https://localhost/evil'})
        self.assertIn('&lt;script&gt;&amp;evil', msg)
        self.assertIn('X&amp;Y', msg)
        self.assertIn('Harga <b>+25.4%</b>', msg)
        self.assertIn('4.0×', msg)
        self.assertIn('BELUM DIVERIFIKASI ON-CHAIN', msg)
        self.assertIn(f'<code>{MINT}</code>', msg)
        self.assertIn('href="https://x.com/a?x=1&amp;b=2"', msg)
        self.assertNotIn('localhost', msg)
        self.assertLess(len(html.unescape(msg)), 1024)
        keyboard = json.loads(links(MINT))['inline_keyboard']
        self.assertEqual(len(keyboard), 2)
        self.assertTrue(any('solscan.io' in x['url'] for row in keyboard for x in row))


class BannerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Store(Path(self.tmp.name) / 'db.sqlite3')
        self.bot = Solter(Config(scanner_mode='market', min_mcap_usd=3000,
                                 max_mcap_usd=300000), self.db)
        self.bot.tg = FakeTelegram()

    async def asyncTearDown(self):
        self.db.close()
        self.tmp.cleanup()

    async def test_pair_banner_used_without_extra_request(self):
        pair = dex_pair(info={'header': 'https://cdn.example/banner.jpg',
                              'imageUrl': 'https://cdn.example/icon.png',
                              'socials': [{'type': 'twitter', 'url': 'https://x.com/real'}]})
        calls = []
        async def only_dex(path):
            calls.append(path)
            return [pair]
        self.bot.dex = only_dex
        await self.bot.market_batch([MINT])
        self.assertEqual(len(calls), 1)
        _, msg, art = self.bot.tg.sent[0]
        self.assertEqual(art, 'https://cdn.example/banner.jpg')
        self.assertIn('href="https://x.com/real"', msg)
        self.assertIsNone(self.bot.rpc)

    async def test_cached_profile_banner_wins_over_pair_icon(self):
        self.bot.market_media[MINT] = (time.time(), {
            'header': 'https://cdn.example/banner-from-profile.jpg',
            'icon': None, 'socials': {}})
        async def only_dex(path):
            return [dex_pair(info={'imageUrl': 'https://cdn.example/icon.png'})]
        self.bot.dex = only_dex
        await self.bot.market_batch([MINT])
        self.assertEqual(self.bot.tg.sent[0][2], 'https://cdn.example/banner-from-profile.jpg')

    async def test_no_banner_is_still_one_text_alert(self):
        async def only_dex(path):
            return [dex_pair()]
        self.bot.dex = only_dex
        await self.bot.market_batch([MINT])
        self.assertEqual(len(self.bot.tg.sent), 1)
        self.assertIsNone(self.bot.tg.sent[0][2])


class MilestoneVisualTests(unittest.TestCase):
    def test_market_milestone_professional_without_invented_safety_or_mcap(self):
        msg = milestone_text('<X&Y>', 2.43, None, 150.0, 7.5,
                             threshold=2, entry_price_sol=.000001,
                             peak_price_sol=.00000243, market_unverified=True, mint=MINT)
        self.assertIn('MILESTONE 2×', msg)
        self.assertIn('2.43×', msg)
        self.assertIn('+143.0%', msg)
        self.assertIn('7 mnt</b> sejak alert', msg)
        self.assertIn('Saat alert', msg)
        self.assertIn('Puncak terpantau', msg)
        self.assertIn('BELUM DIVERIFIKASI ON-CHAIN', msg)
        self.assertIn(f'<code>{MINT}</code>', msg)
        self.assertIn('&lt;X&amp;Y&gt;', msg)
        self.assertNotIn('MC puncak', msg)
        self.assertNotIn('Grade A', msg)
        self.assertLess(len(html.unescape(msg)), 1024)

    def test_chain_milestone_peak_and_risk_message(self):
        msg = milestone_text('SAFE', 10.9, 90.0, 140.0, 18,
                             threshold=10, entry_price_sol=.000003,
                             peak_price_sol=.0000327, mint=MINT)
        self.assertIn('MILESTONE 10×', msg)
        self.assertIn('10.90×', msg)
        self.assertIn('+990.0%', msg)
        self.assertIn('MC puncak estimasi', msg)
        self.assertIn('sejak call', msg)
        self.assertIn('keamanan dapat berubah', msg)

    def test_dev_warning_is_not_rugpull_accusation(self):
        msg = dev_sold_text('<S&>', MINT)
        self.assertIn('PERINGATAN CREATOR', msg)
        self.assertIn('Dev/creator', msg)
        self.assertIn('bukan bukti pasti rug pull', msg)
        self.assertIn('&lt;S&amp;&gt;', msg)
        self.assertIn(f'<code>{MINT}</code>', msg)


class FollowupDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_buttons_and_reply_to_original_alert(self):
        tg = Telegram('token', 'chat', None)
        calls = []
        async def send(method, payload):
            calls.append((method, payload))
            return 'sent', 10
        tg.send = send
        state = await tg.reply('2x', 42, mint=MINT)
        self.assertEqual(state, 'sent')
        self.assertEqual(len(calls), 1)
        payload = calls[0][1]
        self.assertEqual(json.loads(payload['reply_parameters'])['message_id'], 42)
        buttons = json.loads(payload['reply_markup'])['inline_keyboard']
        self.assertTrue(any('gmgn.ai' in b['url'] for row in buttons for b in row))

    async def test_missing_parent_falls_back_without_retrying_uncertain_delivery(self):
        tg = Telegram('token', 'chat', None)
        calls = []
        async def send(method, payload):
            calls.append(dict(payload))
            return ('rejected' if len(calls) == 1 else 'sent'), None
        tg.send = send
        self.assertEqual(await tg.reply('2x', 7, mint=MINT), 'sent')
        self.assertIn('reply_parameters', calls[0])
        self.assertNotIn('reply_parameters', calls[1])
        calls.clear()
        async def timeout(method, payload):
            calls.append(dict(payload))
            return 'uncertain', None
        tg.send = timeout
        self.assertEqual(await tg.reply('2x', 7, mint=MINT), 'uncertain')
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
