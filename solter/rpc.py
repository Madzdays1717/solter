"""JSON-RPC over HTTPS with ceilings this installation enforces on itself."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

import aiohttp

LOG = logging.getLogger('solter')

SWEEP, BACKFILL, FALLBACK, DECISION = 'sweep', 'backfill', 'fallback', 'decision'
# Each kind of work owns a share of the daily ceiling. Past its own share a kind
# may only use capacity no other kind still has reserved for the rest of the day,
# so backfill or fallback can never starve the sweep (the only way new launches
# are found) or the checks a call depends on.
QUOTAS = {SWEEP: .62, BACKFILL: .15, FALLBACK: .05, DECISION: .18}


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
    def __init__(self, url: str, store, per_minute: int, per_day: int, session):
        self.url = url
        self.store = store
        self.per_minute = per_minute
        self.per_day = per_day
        self.session = session
        self.recent: deque[float] = deque()
        self.lock = asyncio.Lock()
        self.failures = 0
        self.decision_waiters = 0

    async def _take(self, kind: str):
        # Never sleep under the global limiter lock. Priority goes to checks
        # needed to validate a potential call, ahead of optional backfill and
        # maintenance, without exceeding the local provider request ceilings.
        if kind == DECISION:
            self.decision_waiters += 1
        try:
            while True:
                async with self.lock:
                    if not allowed(kind, self.store.budget_by_kind(), self.per_day, day_left()):
                        raise NoBudget(f'daily RPC budget reached for {kind}')
                    now = time.monotonic()
                    while self.recent and now - self.recent[0] >= 60:
                        self.recent.popleft()
                    has_capacity = len(self.recent) < self.per_minute
                    priority_ok = kind == DECISION or not self.decision_waiters
                    if has_capacity and priority_ok:
                        self.store.budget_claim(kind)
                        self.recent.append(now)
                        return
                    wait = (max(.05, 60 - (now - self.recent[0])) if not has_capacity else .1)
                # Re-check regularly; new decision work must be able to overtake
                # queued backfill when the next request slot becomes free.
                await asyncio.sleep(min(wait, .25))
        finally:
            if kind == DECISION:
                self.decision_waiters -= 1

    async def call(self, method: str, params: list, kind: str = DECISION):
        """Result of one call, or None. Every HTTP attempt costs one request of the budget."""
        for attempt in range(2):
            await self._take(kind)
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
                        error = body['error']
                        # Agave 4.x returns -32020 when a pagination signature
                        # no longer exists on this RPC node. Retry with a fresh
                        # head query, letting the caller deduplicate signatures.
                        # This is intentionally only for this method/error: never
                        # hide arbitrary RPC errors or bypass the request ceiling.
                        cursor = (method == 'getSignaturesForAddress' and len(params) >= 2
                                  and isinstance(params[1], dict)
                                  and ('before' in params[1] or 'until' in params[1]))
                        if (cursor and isinstance(error, dict) and error.get('code') == -32020
                                and attempt == 0):
                            params = [params[0], {k: v for k, v in params[1].items()
                                                  if k not in ('before', 'until')}]
                            self.store.metric('rpc_cursor_recovered')
                            LOG.info('RPC signature cursor missing; retrying latest signatures')
                            continue
                        LOG.warning('RPC %s error %s', method, str(error)[:160])
                        return None
                    self.failures = 0
                    return body.get('result')
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
                self.failures += 1
                LOG.warning('RPC %s failed: %s', method, type(exc).__name__)
                if attempt == 0:
                    await asyncio.sleep(1)
        return None
