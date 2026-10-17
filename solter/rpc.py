"""JSON-RPC over HTTPS with ceilings this installation enforces on itself."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

import aiohttp

LOG = logging.getLogger('solter')

# Share of the daily ceiling each kind of work may consume, so the cheap
# background sweep can never starve the checks a call depends on.
SWEEP = .6
BACKFILL = .75
FALLBACK = .85
DECISION = 1.0


class NoBudget(RuntimeError):
    pass


class BoundedRPC:
    def __init__(self, url: str, store, per_minute: int, per_day: int, session):
        self.url = url
        self.store = store
        self.per_minute = per_minute
        self.per_day = per_day
        self.session = session
        self.recent: deque[float] = deque()
        self.lock = asyncio.Lock()
        self.failures = 0

    async def _take(self, share: float):
        async with self.lock:
            if self.store.budget_used() >= self.per_day * share:
                raise NoBudget('daily RPC budget reached for this kind of work')
            now = time.monotonic()
            while self.recent and now - self.recent[0] >= 60:
                self.recent.popleft()
            if len(self.recent) >= self.per_minute:
                await asyncio.sleep(max(.05, 60 - (now - self.recent[0])))
            if not self.store.budget_claim(self.per_day):
                raise NoBudget('daily RPC budget reached')
            self.recent.append(time.monotonic())

    async def call(self, method: str, params: list, share: float = DECISION):
        """Result of one call, or None. Every HTTP attempt costs one request of the budget."""
        for attempt in range(2):
            await self._take(share)
            try:
                async with self.session.post(self.url, json={'jsonrpc': '2.0', 'id': 1,
                                                              'method': method, 'params': params},
                                             timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 429:
                        self.failures += 1
                        if attempt == 0:
                            await asyncio.sleep(3)
                            continue
                        return None
                    if resp.status >= 400:
                        LOG.warning('RPC HTTP %s on %s', resp.status, method)
                        return None
                    body = await resp.json(content_type=None)
                    if not isinstance(body, dict):
                        return None
                    if body.get('error'):
                        LOG.warning('RPC %s error %s', method, str(body['error'])[:160])
                        return None
                    self.failures = 0
                    return body.get('result')
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
                self.failures += 1
                LOG.warning('RPC %s failed: %s', method, type(exc).__name__)
                if attempt == 0:
                    await asyncio.sleep(1)
        return None
