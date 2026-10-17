"""Bounded provider HTTP: prioritize successful calls over flooding free APIs."""

import asyncio
import logging
import time
from urllib.parse import urlparse

import aiohttp

LOG = logging.getLogger('solter.http')


class RequestPacer:
    """Simple per-provider limiter; accepts occasional bursts, applies 429 cooldown."""
    def __init__(self, interval: float):
        self.interval = interval
        self.next_allowed = 0.0
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            now = time.monotonic()
            delay = max(0, self.next_allowed - now)
            self.next_allowed = max(now, self.next_allowed) + self.interval
        if delay:
            await asyncio.sleep(delay)

    def cooldown(self, seconds: float):
        self.next_allowed = max(self.next_allowed, time.monotonic() + min(60, max(1, seconds)))


class PublicHTTP:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        # Deliberately below documented public endpoints' rates; no global blocking of other feeds.
        self.pacers = {
            'api.dexscreener.com': RequestPacer(.30),
            'api.geckoterminal.com': RequestPacer(7.0),
        }
        self.slots = asyncio.Semaphore(5)

    async def get(self, url: str):
        host = urlparse(url).hostname
        pacer = self.pacers.get(host)
        if pacer is None:
            raise ValueError('Unknown public API host')
        await pacer.wait()
        async with self.slots:
            try:
                async with self.session.get(url) as response:
                    if response.status == 429:
                        retry = response.headers.get('Retry-After', '12')
                        try:
                            seconds = float(retry)
                        except ValueError:
                            seconds = 12
                        pacer.cooldown(seconds)
                        LOG.warning('%s is rate limiting requests', host)
                        return None
                    if response.status != 200:
                        LOG.warning('%s returned HTTP %s', host, response.status)
                        return None
                    return await response.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
                LOG.warning('%s unavailable: %s', host, type(error).__name__)
                return None
