"""Independent market-only discovery and poll workers.

Market inventory, not limited Python sets, owns candidate scheduling.
No RPC is involved. Requests are paced by Solter.dex and data arrival drives
market decisions in Solter.market_batch. One 30-token batch per turn bounds
latency and leaves quota for price tracking and discovery sources.
"""
from __future__ import annotations

import asyncio
import logging
import time

from .market import GECKO, gecko_mints
from .market_media import extract_profile_media
from .sources import dex_list_mints

LOG = logging.getLogger('solter')


class MarketRuntime:
    def __init__(self, bot):
        self.bot = bot
        self.queue = bot.market_inventory
        self.queue.seed_existing()

    async def discover_once(self, now: float):
        bot = self.bot
        urls = (f'{GECKO}/networks/solana/trending_pools?duration=5m&include=base_token',
                f'{GECKO}/networks/solana/new_pools?include=base_token')
        gecko = await asyncio.gather(*(bot.http_json(url) for url in urls))
        for source, payload in zip(('trending', 'new_pool'), gecko):
            if payload is None:
                bot.db.metric('market_gecko_unavailable')
                continue
            mints = gecko_mints(payload)
            self.queue.add(mints, source, now, priority=2 if source == 'trending' else 1)
            bot.db.metric('market_candidates_ingested', len(mints))
        profile_feeds = await asyncio.gather(*(bot.dex(endpoint) for endpoint in
            ('/token-profiles/latest/v1', '/token-boosts/top/v1')))
        for source, feed in zip(('profile', 'boosted'), profile_feeds):
            mints = dex_list_mints(feed)
            self.queue.add(mints, source, now, priority=1)
            bot.db.metric('market_candidates_ingested', len(mints))
            for entry in (feed if isinstance(feed, list) else []):
                item = extract_profile_media(entry)
                if item:
                    mint, media = item
                    old = bot.market_media.get(mint, (0, {}))[1]
                    bot.market_media[mint] = (now, {
                        'header': media.get('header') or old.get('header'),
                        'icon': media.get('icon') or old.get('icon'),
                        'socials': {**old.get('socials', {}), **media.get('socials', {})}})
        # Cache is for banner enrichment, never a candidate inventory.
        if len(bot.market_media) > 1200:
            bot.market_media = dict(sorted(bot.market_media.items(),
                key=lambda kv: kv[1][0], reverse=True)[:600])
        bot.market_fast_wakeup.set()

    async def discovery_loop(self):
        while self.bot.running:
            began = time.monotonic()
            try:
                await self.discover_once(time.time())
            except Exception:
                LOG.exception('market feed discovery failed')
                self.bot.db.metric('market_scan_error')
            await asyncio.sleep(max(1, self.bot.cfg.market_scan_seconds - (time.monotonic() - began)))

    async def scan_once(self):
        bot = self.bot
        now = time.time()
        mints = self.queue.due(now, 30)
        if not mints:
            return False
        result = await bot.market_batch(mints)
        when = time.time()
        for mint in mints:
            token = bot.db.token(mint) if result is not None and mint in result else None
            self.queue.result(mint, when, indexed=token is not None,
                              token=token, failed=result is None,
                              reason_override=bot.market_rejected.get(mint),
                              hot_refresh=bot.cfg.market_fast_seconds)
        bot.db.metric('market_batch_count')
        bot.db.metric('market_polled_tokens', len(mints))
        return True

    async def scan_loop(self):
        while self.bot.running:
            try:
                did_work = await self.scan_once()
            except Exception:
                LOG.exception('market queue scan failed')
                self.bot.db.metric('market_scan_error')
                did_work = False
            # No global 15-second delay. DEX's rate limiter and HTTP response
            # pace real polling; if nothing is due sleep/wake briefly.
            if did_work:
                await asyncio.sleep(0)
                continue
            self.bot.market_fast_wakeup.clear()
            try:
                await asyncio.wait_for(self.bot.market_fast_wakeup.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
