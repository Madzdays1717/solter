"""JSON-RPC over HTTPS with ceilings this installation enforces on itself."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque, defaultdict
from email.utils import parsedate_to_datetime

import aiohttp

LOG = logging.getLogger('solter')

SWEEP, BACKFILL, FALLBACK, DECISION = 'sweep', 'backfill', 'fallback', 'decision'
# Each kind of work owns a share of the daily ceiling. Past its own share a kind
# may only use capacity no other kind still has reserved for the rest of the day,
# so backfill or fallback can never starve the sweep (the only way new launches
# are found) or the checks a call depends on.
QUOTAS = {SWEEP: .45, BACKFILL: .15, FALLBACK: .15, DECISION: .25}


class NoBudget(RuntimeError):
    pass


def day_left(now: float | None = None) -> float:
    """Fraction of the current UTC day still to come (1.0 at midnight)."""
    now = time.time() if now is None else now
    return 1 - (now % 86400) / 86400


def allowed(kind: str, used: dict, per_day: int, left: float) -> bool:
    """Whether one more request of `kind` fits today's ceiling and everyone else's reservation."""
    total = sum(used.values())
    if total >= per_day:
        return False
    if used.get(kind, 0) < per_day * QUOTAS.get(kind, 0):
        return True
    reserved = sum(min(max(0.0, per_day * share - used.get(other, 0)), per_day * share * left)
                   for other, share in QUOTAS.items() if other != kind)
    return total < per_day - reserved


class BoundedRPC:
    def __init__(self, url: str, store, per_minute: int, per_day: int, session, fallback_url: str = '', *, rps: float = 5, method_rps: float = 2):
        self.url = url
        self.fallback_url = fallback_url
        self.store = store
        self.per_minute = per_minute
        self.per_day = per_day
        self.session = session
        self.recent: deque[float] = deque()
        self.lock = asyncio.Lock()
        self.failures = 0
        self.rps, self.method_rps = rps, method_rps
        self.next_global = 0.0
        self.next_method = defaultdict(float)
        self.cooldown = 0.0

    async def _take(self, kind: str, method: str = ''):
        while True:
            async with self.lock:
                if not allowed(kind, self.store.budget_by_kind(), self.per_day, day_left()):
                    raise NoBudget(f'daily RPC budget reached for {kind}')
                now = time.monotonic()
                while self.recent and now - self.recent[0] >= 60:
                    self.recent.popleft()
                due = max(self.next_global, self.next_method[method], self.cooldown)
                if len(self.recent) >= self.per_minute:
                    due = max(due, self.recent[0] + 60)
                if now >= due:
                    self.store.budget_claim(kind)
                    self.recent.append(now)
                    self.next_global = now + 1 / self.rps
                    self.next_method[method] = now + 1 / self.method_rps
                    return
                delay = due - now
            # Do not hold the lock while sleeping: other methods can progress.
            await asyncio.sleep(min(delay, 1.0))

    def throttle(self, value):
        try:
            delay = float(value)
        except (ValueError, TypeError):
            try:
                delay = parsedate_to_datetime(value).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                delay = 3.0
        self.cooldown = max(self.cooldown, time.monotonic() + max(1.0, delay))
        self.store.metric('rpc_429')

    async def call(self, method: str, params: list, kind: str = DECISION):
        """Result of one call, or None. Every HTTP attempt costs one request of the budget."""
        for attempt in range(2):
            await self._take(kind, method)
            try:
                async with self.session.post(self.fallback_url if attempt and self.fallback_url else self.url, json={'jsonrpc': '2.0', 'id': 1,
                                                              'method': method, 'params': params},
                                             timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 429:
                        self.throttle(getattr(resp, 'headers', {}).get('Retry-After'))
                        self.failures += 1
                        if attempt == 0:
                            await asyncio.sleep(3)
                            continue
                        return None
                    if resp.status >= 500 and attempt == 0:
                        self.store.metric('rpc_server_retry')
                        await asyncio.sleep(1)
                        continue
                    if resp.status == 403:
                        self.cooldown = max(self.cooldown, time.monotonic() + 300)
                    if resp.status >= 400:
                        LOG.warning('RPC HTTP %s on %s', resp.status, method)
                        return None
                    body = await resp.json(content_type=None)
                    if not isinstance(body, dict):
                        return None
                    if body.get('error'):
                        error = body['error']
                        code = error.get('code') if isinstance(error, dict) else None
                        LOG.warning('RPC %s error code=%s', method, code)
                        self.store.metric('rpc_error:' + method)
                        if attempt == 0 and (self.fallback_url or code in (-32005, -32004, -32009)):
                            await asyncio.sleep(1)
                            continue
                        return None
                    self.failures = 0
                    return body.get('result')
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
                self.failures += 1
                LOG.warning('RPC %s failed: %s', method, type(exc).__name__)
                if attempt == 0:
                    await asyncio.sleep(1)
        return None
