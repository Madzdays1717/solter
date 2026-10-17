"""One scanner. Independent discovery sources, fair polling, instant decisions.

No RPC, trade-history backfill, wallet tracker or post-alert price polling.
The only unavoidable waits are public network responses and provider pacing.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from .database import Database
from .http_client import PublicHTTP
from .market_data import dex_profiles, gecko_tokens, normalize_pair, pump_event
from .settings import Settings
from .signals import Decision, MarketSnapshot, evaluate
from .telegram import Telegram

LOG = logging.getLogger('solter')
DEX = 'https://api.dexscreener.com'
GECKO = 'https://api.geckoterminal.com/api/v2'
PUMP = 'wss://pumpportal.fun/api/data'


class Scanner:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        self.pending_sends: set[asyncio.Task] = set()

    async def run(self):
        if not self.settings.telegram_ready:
            raise ValueError('Telegram belum dikonfigurasi di .env; isi token dan chat ID.')
        self.database.restore_known_tokens()
        timeout = aiohttp.ClientTimeout(total=10, connect=6)
        connector = aiohttp.TCPConnector(limit=16, ttl_dns_cache=300)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector,
                   headers={'User-Agent': 'Solter/3.0 public-market-scanner'}) as session:
            http = PublicHTTP(session)
            telegram = Telegram(session, self.settings.token, self.settings.chat_id)
            tasks = [
                asyncio.create_task(self.supervise('PumpPortal', lambda: self.discover_pump(session)), name='pump-discovery'),
                asyncio.create_task(self.supervise('GeckoTerminal', lambda: self.discover_gecko(http)), name='gecko-discovery'),
                asyncio.create_task(self.supervise('DexScreener discovery', lambda: self.discover_dex(http)), name='dex-discovery'),
                asyncio.create_task(self.supervise('Market scanner', lambda: self.scan_market(http, telegram)), name='market-scanner'),
                asyncio.create_task(self.supervise('Heartbeat', self.heartbeat), name='heartbeat'),
            ]
            LOG.info('Solter started: 1 market scanner, 3 free discovery sources, no RPC')
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if self.pending_sends:
                    _, pending = await asyncio.wait(self.pending_sends, timeout=10)
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)

    async def supervise(self, name: str, worker):
        """A bad provider payload must never terminate other scanner workers."""
        while True:
            try:
                await worker()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception('%s worker crashed; restarting in 5 seconds', name)
            await asyncio.sleep(5)

    async def discover_pump(self, session: aiohttp.ClientSession):
        retry = 2
        while True:
            try:
                async with session.ws_connect(PUMP, heartbeat=25, timeout=9,
                                              receive_timeout=90) as ws:
                    await ws.send_json({'method': 'subscribeNewToken'})
                    await ws.send_json({'method': 'subscribeMigration'})
                    retry = 2
                    LOG.info('PumpPortal discovery connected')
                    async for event in ws:
                        if event.type == aiohttp.WSMsgType.TEXT:
                            parsed = pump_event(event.data)
                            if parsed:
                                source, mint = parsed
                                self.database.ingest([mint], source,
                                    priority=3 if source == 'migration' else 2)
                                # Each event is already persisted by ingest; avoid a second
                                # SQLite transaction on this high-volume feed.
                        elif event.type in (aiohttp.WSMsgType.ERROR,
                                            aiohttp.WSMsgType.CLOSED):
                            break
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                LOG.warning('PumpPortal reconnect needed: %s', type(error).__name__)
            await asyncio.sleep(retry)
            retry = min(45, retry * 2)

    async def discover_gecko(self, http: PublicHTTP):
        feeds = (
            ('gecko_trending', f'{GECKO}/networks/solana/trending_pools?duration=5m&include=base_token', 2),
            ('gecko_new', f'{GECKO}/networks/solana/new_pools?include=base_token', 1),
        )
        while True:
            for name, url, priority in feeds:
                payload = await http.get(url)
                if payload is None:
                    self.database.increment('gecko_errors')
                else:
                    count = self.database.ingest(gecko_tokens(payload), name, priority)
                    self.database.increment('discovered_gecko', count)
            await asyncio.sleep(75)

    async def discover_dex(self, http: PublicHTTP):
        feeds = (
            ('dex_profiles', '/token-profiles/latest/v1', 1),
            ('dex_boosts', '/token-boosts/latest/v1', 1),
            ('dex_trending_boosts', '/token-boosts/top/v1', 2),
            ('dex_community', '/community-takeovers/latest/v1', 1),
        )
        while True:
            for name, path, priority in feeds:
                payload = await http.get(DEX + path)
                if payload is None:
                    self.database.increment('dex_discovery_errors')
                    continue
                profiles = dex_profiles(payload)
                count = self.database.ingest(list(profiles), name, priority, profiles)
                self.database.increment('discovered_dex', count)
            await asyncio.sleep(70)

    async def scan_market(self, http: PublicHTTP, telegram: Telegram):
        while True:
            batch = self.database.due(30)
            if not batch:
                await asyncio.sleep(1)
                continue
            url = DEX + '/tokens/v1/solana/' + ','.join(batch)
            pairs = await http.get(url)
            if not isinstance(pairs, list):
                for mint in batch:
                    self.database.result(mint, None, unavailable=True)
                self.database.increment('market_errors')
                await asyncio.sleep(1)
                continue
            self.database.increment('market_batches')
            self.database.increment('tokens_evaluated', len(batch))
            grouped: dict[str, list] = {mint: [] for mint in batch}
            # Normalization accepts only valid pairs; never trust token names/symbols.
            observed = time.time()
            for pair in pairs:
                if not isinstance(pair, dict):
                    continue
                base = pair.get('baseToken')
                mint = base.get('address') if isinstance(base, dict) else None
                if mint in grouped:
                    snapshot = normalize_pair(pair, mint, observed)
                    if snapshot:
                        grouped[mint].append(snapshot)
            for mint, snapshots in grouped.items():
                if not snapshots:
                    self.database.result(mint, None)
                    continue
                # A token can have multiple pools; don't hide an active pool
                # solely because the deepest pool is currently quiet.
                assessed = [(evaluate(s), s) for s in snapshots]
                eligible = [(d, s) for d, s in assessed if d.accepted]
                if eligible:
                    decision, snapshot = max(eligible, key=lambda item: (
                        item[0].strength, item[1].liquidity))
                else:
                    decision, snapshot = max(assessed, key=lambda item: item[1].liquidity)
                self.database.result(mint, snapshot, decision)
                if not decision.accepted:
                    self.database.increment('rejected')
                    continue
                if not self.database.reserve_alert(mint, decision):
                    continue
                self.database.increment('signal_qualified')
                image = self.database.trace(mint)['candidate'].get('image_url') or snapshot.image
                job = asyncio.create_task(self.deliver(telegram, snapshot, decision, image))
                self.pending_sends.add(job)
                job.add_done_callback(self.pending_sends.discard)
            # No 15/65-second global evaluation sleep: provider rate pacing is
            # the only delay between ready batches.
            await asyncio.sleep(0)

    async def deliver(self, telegram: Telegram, snapshot: MarketSnapshot,
                      decision: Decision, image: str | None):
        try:
            state, message_id = await telegram.send(snapshot, decision, image)
        except Exception:
            LOG.exception('Unexpected Telegram delivery failure for %s', snapshot.mint)
            state, message_id = 'uncertain', None
        self.database.alert_result(snapshot.mint, state, message_id)
        self.database.increment('telegram_' + state)
        if state == 'sent':
            LOG.info('CALL %s %s +%.1f%% MC=$%.0f', snapshot.symbol,
                     snapshot.mint, snapshot.change_5m, snapshot.market_cap)
        else:
            LOG.warning('Alert %s: %s; inspect via trace', snapshot.mint, state)

    async def heartbeat(self):
        while True:
            await asyncio.sleep(60)
            stats = self.database.stats()
            LOG.info('SCAN candidates=%d due=%d delivered=%d failed=%d',
                     stats['candidates'], stats['due_now'],
                     stats['alerts'].get('sent', 0),
                     stats['counters'].get('market_errors', 0))
