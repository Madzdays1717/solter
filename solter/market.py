"""Bounded market enrichment for promising Pump.fun tokens."""

import asyncio
from dataclasses import dataclass
import logging
import time

import aiohttp

from .addresses import valid_address
from .events import number
from .metadata import Profile, parse_profile, public_url

LOG = logging.getLogger('solter.market')
PUMP_COIN = 'https://frontend-api-v3.pump.fun/coins-v2/'
DEX_TOKENS = 'https://api.dexscreener.com/tokens/v1/solana/'


@dataclass(frozen=True)
class MarketSnapshot:
    observed_at: float
    market_cap_usd: float = 0.0
    liquidity_usd: float = 0.0
    volume_5m_usd: float = 0.0
    buys_5m: int = 0
    sells_5m: int = 0
    image: str = ''
    banner: str = ''
    twitter: str = ''
    telegram: str = ''
    website: str = ''
    source: str = ''


class MarketReader:
    """Fetches only for selected candidates; never holds the trade event loop.

    One request per candidate may contain partial data. A missing response is not
    interpreted as a healthy market or an unsafe market.
    """

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.semaphore = asyncio.Semaphore(3)
        self.cache: dict[str, MarketSnapshot] = {}
        self.inflight: dict[str, asyncio.Task] = {}
        self.requests: list[float] = []

    def cached(self, mint: str, *, max_age: float = 12) -> MarketSnapshot | None:
        entry = self.cache.get(mint)
        return entry if entry and time.time() - entry.observed_at < max_age else None

    async def read(self, mint: str, phase: str, *, timeout: float = 1.7) -> MarketSnapshot | None:
        cached = self.cached(mint)
        if cached:
            return cached
        task = self.inflight.get(mint)
        if task is None and len(self.inflight) >= 24:
            return None
        if task is None:
            task = asyncio.create_task(self._fetch(mint, phase))
            self.inflight[mint] = task
            task.add_done_callback(lambda _task, m=mint: self.inflight.pop(m, None))
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def _fetch(self, mint: str, phase: str) -> MarketSnapshot | None:
        if not valid_address(mint):
            return None
        async with self.semaphore:
            now = time.monotonic()
            self.requests = [stamp for stamp in self.requests if now - stamp < 60]
            # Local service protection; well below the published DEX API ceiling.
            if len(self.requests) >= 45:
                return None
            self.requests.append(now)
            endpoint = DEX_TOKENS + mint if phase == 'PumpSwap' else PUMP_COIN + mint
            try:
                async with self.session.get(endpoint,
                        timeout=aiohttp.ClientTimeout(total=2.5)) as response:
                    if response.status != 200:
                        LOG.debug('Market source %d for %s', response.status, mint)
                        return None
                    data = await response.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                return None
            observed_at = time.time()
            snap = (self._dex(data, mint, observed_at)
                    if phase == 'PumpSwap' else self._pump(data, mint, observed_at))
            if snap:
                self.cache[mint] = snap
                # Remove expired entries to avoid unbounded memory growth.
                if len(self.cache) > 600:
                    self.cache = {key: value for key, value in self.cache.items()
                                  if observed_at - value.observed_at < 60}
            return snap

    @staticmethod
    def _pump(data: object, mint: str, stamp: float) -> MarketSnapshot | None:
        if not isinstance(data, dict) or data.get('mint') != mint:
            return None
        profile: Profile | None = parse_profile(data, pump=True)
        if profile is None:
            return None
        return MarketSnapshot(
            observed_at=stamp, market_cap_usd=profile.market_cap_usd,
            image=profile.image, banner=profile.banner,
            twitter=profile.twitter, telegram=profile.telegram,
            website=profile.website, source='Pump.fun',
        )

    @staticmethod
    def _dex(data: object, mint: str, stamp: float) -> MarketSnapshot | None:
        if not isinstance(data, list):
            return None
        pools = []
        for pair in data:
            if not isinstance(pair, dict) or pair.get('chainId') != 'solana':
                continue
            if 'pump' not in str(pair.get('dexId') or '').lower():
                continue
            base = pair.get('baseToken') or {}
            if not isinstance(base, dict) or base.get('address') != mint:
                continue
            liquidity = pair.get('liquidity') or {}
            if not isinstance(liquidity, dict):
                continue
            pools.append((number(liquidity.get('usd')), pair))
        if not pools:
            return None
        _, pair = max(pools, key=lambda item: item[0])
        transaction_windows = pair.get('txns')
        if not isinstance(transaction_windows, dict):
            transaction_windows = {}
        txns = transaction_windows.get('m5') or {}
        if not isinstance(txns, dict):
            txns = {}
        info = pair.get('info')
        if not isinstance(info, dict):
            info = {}
        socials = info.get('socials') or []
        if not isinstance(socials, list):
            socials = []
        def social(which):
            for row in socials:
                if isinstance(row, dict) and str(row.get('type') or row.get('platform')).lower() == which:
                    return public_url(row.get('url') or row.get('handle'))
            return ''
        sites = info.get('websites') or []
        if not isinstance(sites, list):
            sites = []
        website = next((public_url(x.get('url')) for x in sites if isinstance(x, dict)
                        and public_url(x.get('url'))), '')
        return MarketSnapshot(
            observed_at=stamp, market_cap_usd=number(pair.get('marketCap')),
            liquidity_usd=number((pair.get('liquidity') or {}).get('usd')),
            volume_5m_usd=number((pair.get('volume') or {}).get('m5')),
            buys_5m=int(number(txns.get('buys'))), sells_5m=int(number(txns.get('sells'))),
            image=public_url(info.get('imageUrl')), banner=public_url(info.get('header')),
            twitter=social('twitter'), telegram=social('telegram'), website=website,
            source='DexScreener · PumpSwap',
        )
