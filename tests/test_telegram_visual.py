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
from solter.telegram import Telegram, links, market_alert_text
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


if __name__ == '__main__':
    unittest.main()
