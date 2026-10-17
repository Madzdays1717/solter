"""Solter v2 engine.

launch (PumpPortal, free) -> reservoir sweep (getMultipleAccounts, 100 curves per
request) -> live watch of the fastest inflow (logsSubscribe, trades decoded from
logs at no RPC cost) -> flow gates -> deep checks (mint, curve/pool, holders,
dev, creator, socials) -> one Telegram call -> outcome tracking and follow-ups.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from urllib.parse import quote
import time
from collections import deque
from dataclasses import replace

import aiohttp

from . import __version__
from .chain import (CURVE_BYTES, PUMP_AMM, PUMP_PROGRAM, PUMP_SUPPLY, TOKEN_PROGRAM, TOKEN_UNIT, WSOL,
                    associated_account, bonding_curve, curve_account, migration_pool, mint_safety,
                    mint_supply, pool_account)
from .config import Config
from .events import (Trade, cpi_payloads, decode_amm_trade, decode_pump_trade, log_payloads,
                     trades_from_logs, trades_from_tx)
from .flow import Flow, evaluate
from .quality import Assessment, holder_stats, market_cap_reason, quality_reason, score, socials_from
from .reservoir import candidates, classify, dev_buy_pct
from .rpc import BACKFILL, DECISION, FALLBACK, QUOTAS, SWEEP, BoundedRPC, NoBudget
from .sources import DEX, PUMP_WS, pair_facts, parse_portal, pumpswap_pair, sol_usd_from, dex_list_mints
from .market import GECKO, by_mint, gecko_mints, market_snapshot, market_strength, market_ready
from .market_signal import evaluate_market
from .market_media import extract_profile_media, combine_media
from .report import pulse
from .storage import HORIZONS, Store
from .telegram import Telegram, call_text, market_alert_text, dev_sold_text, milestone_text

LOG = logging.getLogger('solter')
TX_OPTIONS = {'encoding': 'jsonParsed', 'commitment': 'confirmed', 'maxSupportedTransactionVersion': 1}
MILESTONES = (2, 3, 5, 10)
METADATA_RETRY = 300    # a metadata fetch that failed is tried again after this
CHECK_TTL = 45          # seconds a passing deep check is reused
FAILED_CHECK_TTL = 120  # confirmed quality failures can be reused briefly
HOLDER_RETRY_TTL = 20  # unavailable RPC data is not evidence that holders are unsafe
COOLDOWN = 40          # seconds before a demoted token may be watched again


class Solter:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.db = store
        self.rules = cfg.rules()
        self.quality = cfg.quality_rules()
        # A traction reading older than a few sweeps no longer says anything about now.
        self.reservoir = replace(cfg.reservoir_rules(), stale_after=max(300.0, 3 * self.sweep_interval(2)))
        self.session = None
        self.rpc: BoundedRPC | None = None
        self.tg: Telegram | None = None
        self.running = True
        self.fallback: asyncio.Queue = asyncio.Queue(maxsize=300)
        self.queued: set = set()
        self.backfills: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.backfill_queued: set = set()
        self.refresh: set = set()
        self.sol_usd: float | None = None
        self.ws_up = False
        self.ws_down_at: float | None = time.time()   # the process start counts as an outage
        self.checked: dict = {}
        self.metadata: dict = {}
        self.pairs: dict = {}
        self.reasons: dict = {}
        self.flow_qualified: set[str] = set()
        self.quality_qualified: set[str] = set()
        self.dex_lock = asyncio.Lock()
        self.last_dex = 0.0
        self.lags: deque = deque(maxlen=200)    # event timestamp minus arrival time, live trades
        self.clock_warned = 0.0
        self.market_seen: dict[str, float] = {}
        self.market_media: dict[str, tuple[float, dict]] = {}  # cached public artwork, no fetch on call
        # Live migrations wake the free DEX snapshot refresh without any RPC work.
        self.market_urgent: set[str] = set()
        self.market_fast_wakeup = asyncio.Event()
        # Independent evaluations: a slow RPC for one mint must never block the others.
        self.eval_jobs: dict[str, asyncio.Task] = {}
        self.eval_wakeup = asyncio.Event()
        self.eval_last: dict[str, tuple[float, float | None]] = {}
        self.eval_revision: dict[str, int] = {}
        self.event_received: dict[str, float] = {}
        self.scheduler_wakeup = asyncio.Event()
        self.metadata_jobs: dict[str, asyncio.Task] = {}
        self.metadata_slots = asyncio.Semaphore(2)
        # Do not impose another four-token line in front of the RPC limiter.
        # Actual outbound requests are still bounded by BoundedRPC/provider limits.
        self.eval_rpc_slots = asyncio.Semaphore(max(4, min(16, cfg.max_watch)))

    def attach(self, session):
        self.session = session
        # Market-only mode does not even instantiate an RPC client.
        self.rpc = (BoundedRPC(self.cfg.rpc_http, self.db, self.cfg.rpc_per_minute,
                               self.cfg.rpc_per_day, session)
                    if self.cfg.scanner_mode == 'chain' else None)
        self.tg = Telegram(self.cfg.tg_token, self.cfg.tg_chat, session)

    # discovery -----------------------------------------------------------
    async def discover(self):
        backoff = 2
        while self.running:
            try:
                portal_url = PUMP_WS + ('?api-key=' + quote(self.cfg.portal_key, safe='')
                                          if self.cfg.portal_key else '')
                async with self.session.ws_connect(portal_url, heartbeat=25, timeout=20,
                                                   max_msg_size=2**20) as ws:
                    LOG.info('PumpPortal connected (free launch and migration streams)')
                    self.db.metric('portal_connect')
                    await ws.send_json({'method': 'subscribeNewToken'})
                    await ws.send_json({'method': 'subscribeMigration'})
                    backoff = 2
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            break
                        self.on_portal(msg.data, time.time())
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                LOG.warning('PumpPortal disconnected: %s', type(exc).__name__)
            await asyncio.sleep(backoff + random.random())
            backoff = min(backoff * 2, 90)

    def on_portal(self, text, now: float):
        parsed = parse_portal(text)
        if not parsed:
            return
        kind, info = parsed
        if kind == 'migrate':
            self.on_migration(info['mint'], now)
            return
        try:
            account = bonding_curve(info['mint'])
        except ValueError:
            return
        if self.db.discover(info, account, now):
            self.db.metric('launch_seen')
            self.scheduler_wakeup.set()

    def on_migration(self, mint: str, now: float):
        try:
            pool = migration_pool(mint)
        except ValueError:
            return
        was = self.db.migrate(mint, pool, now)
        self.db.metric('migration_seen')
        self.scheduler_wakeup.set()
        if self.cfg.scanner_mode == 'market':
            if len(self.market_urgent) < 300:
                self.market_urgent.add(mint)
                self.market_fast_wakeup.set()
            else:
                # Still discoverable from the persisted migration inventory.
                self.db.metric('market_urgent_overflow')
        elif was == 'watching':
            self.request_backfill(mint)
        LOG.info('MIGRATION %s (was %s)', mint[:12], was)

    # reservoir sweep -----------------------------------------------------
    def sweep_interval(self, requests: int) -> float:
        """Never let a whole day of sweeping use more than the sweep's share of the budget."""
        return max(self.cfg.sweep_seconds,
                   86400 * max(1, requests) / max(1.0, self.cfg.rpc_per_day * QUOTAS[SWEEP]))

    async def sweep(self):
        while self.running:
            requests = 0
            try:
                requests = await self.sweep_once(time.time())
            except NoBudget:
                self.db.metric('sweep_paused')
                LOG.warning('Sweep paused: its share of today\'s RPC budget is used')
            await asyncio.sleep(self.sweep_interval(requests))

    async def sweep_once(self, now: float) -> int:
        batch = self.db.sweep_batch(now, self.cfg.sweep_accounts, self.reservoir.first_check,
                                    self.reservoir.recheck)
        requests = 0
        for start in range(0, len(batch), 100):
            chunk = batch[start:start + 100]
            result = await self.rpc.call('getMultipleAccounts', [
                [t['account'] for t in chunk],
                {'encoding': 'base64', 'commitment': 'confirmed',
                 'dataSlice': {'offset': 0, 'length': CURVE_BYTES}}], kind=SWEEP)
            requests += 1
            values = result.get('value') if isinstance(result, dict) else None
            if not isinstance(values, list) or len(values) != len(chunk):
                continue
            checked_at = time.time()
            changes, counts = [], {}
            for token, account in zip(chunk, values):
                updates = classify(token, curve_account(account), checked_at, self.reservoir)
                changes.append((token['mint'], updates))
                state = updates.get('state')
                if state and state != token['state']:
                    key = f'reservoir_{state}'
                    counts[key] = counts.get(key, 0) + 1
            self.db.update_many(changes)
            if any(item.get('state') == 'alive' for _, item in changes):
                self.scheduler_wakeup.set()
            if counts:
                self.db.metric_many(counts)
        return requests

    # live watch slots ----------------------------------------------------
    async def scheduler(self):
        # Incoming discovery/sweep events wake this worker immediately; the
        # timeout is only a housekeeping fallback, never the hot-path trigger.
        while self.running:
            self.scheduler_wakeup.clear()
            try:
                self.schedule(time.time())
            except Exception:
                LOG.exception('scheduler failed')
            try:
                await asyncio.wait_for(self.scheduler_wakeup.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass

    def schedule(self, now: float):
        for t in self.db.watching():
            started = t['watch_started'] or now
            idle = self.chain_now(now) - (t['last_trade_at'] or started)
            if now >= (t['watch_until'] or 0):
                self.db.demote(t['mint'], now, COOLDOWN, 'watched without a call')
                self.db.metric('watch_ended')
            elif now - started > 65 and idle > 65:
                self.db.demote(t['mint'], now, COOLDOWN, 'flow went quiet')
                self.db.metric('watch_quiet')
        active = self.db.watching()
        free = self.cfg.max_watch - len(active)
        ready = candidates(self.db.alive(), now, self.reservoir, self.db.launches)
        if free <= 0 and ready:
            # Only replace quiet, sufficiently old watches with much stronger demand.
            # Never interrupt a watch that has just seen real trades.
            quiet = [t for t in active if now - (t['watch_started'] or now) >= 25
                     and self.chain_now(now) - (t['last_trade_at'] or t['watch_started'] or now) >= 22]
            weakest = min(quiet, key=lambda t: (t['traction'] or 0), default=None)
            top = ready[0]
            migrant_slots = max(1, self.cfg.max_watch * 2 // 3)
            migrant_active = sum(1 for t in active if t['stage'] == 'migrated')
            bonding_ready = any(t['stage'] != 'migrated' for t in ready)
            allowed_top = (top['stage'] != 'migrated' or not bonding_ready
                           or migrant_active < migrant_slots)
            candidate_strength = (market_strength(top) if top['stage'] == 'migrated'
                                  else (top.get('traction') or 0) * 10)
            weakest_strength = (market_strength(weakest) if weakest and weakest['stage'] == 'migrated'
                                else (weakest.get('traction') or 0) * 10 if weakest else 0)
            if weakest and allowed_top and candidate_strength > max(4, weakest_strength * 1.8):
                self.db.demote(weakest['mint'], now, COOLDOWN, 'preempted by stronger flow')
                self.db.metric('watch_preempted')
                active = self.db.watching()
                free = self.cfg.max_watch - len(active)
        if free <= 0:
            return
        migrated_cap = max(1, self.cfg.max_watch * 2 // 3)
        migrated = sum(1 for t in active if t['stage'] == 'migrated')
        for t in ready:
            if free <= 0:
                break
            if self.db.has_call(t['mint']):
                self.db.update(t['mint'], state='called')
                continue
            if t['stage'] == 'migrated':
                if migrated >= migrated_cap and any(r['stage'] != 'migrated' for r in ready):
                    continue
                migrated += 1
            if self.db.promote(t['mint'], now, self.cfg.watch_seconds):
                free -= 1
                self.db.metric('promoted')
                if t['stage'] == 'migrated' and t.get('market_at'):
                    LOG.info('WATCH %s %s stage=migrated · DEX 5m: %d buy/%d sell, '
                             '$%.0f volume · awaiting verified on-chain trades',
                             t['mint'][:12], (t['symbol'] or '')[:16],
                             t.get('market_buys') or 0, t.get('market_sells') or 0,
                             t.get('market_volume_usd') or 0)
                else:
                    LOG.info('WATCH %s %s initial traction %.2f SOL/min stage=%s',
                             t['mint'][:12], (t['symbol'] or '')[:16],
                             t['traction'] or 0, t['stage'])
                self.request_backfill(t['mint'])
                self.prefetch_metadata(t)
                self.kick_evaluation(t['mint'], trade=False)

    # Solana logs ---------------------------------------------------------
    async def logs(self):
        backoff = 2
        while self.running:
            try:
                async with self.session.ws_connect(self.cfg.rpc_ws, heartbeat=25, timeout=20,
                                                   max_msg_size=2**21) as ws:
                    LOG.info('Solana logs WebSocket connected')
                    self.ws_up = True
                    self.on_ws_restored()
                    backoff = 2
                    await self.serve_logs(ws)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                LOG.warning('Solana WebSocket reconnect: %s', type(exc).__name__)
            finally:
                if self.ws_up:
                    self.ws_down_at = time.time()
                self.ws_up = False
            await asyncio.sleep(backoff + random.random())
            backoff = min(backoff * 2, 90)

    async def serve_logs(self, ws):
        request = 0
        active: dict = {}     # mint -> (account, stage, subscription)
        pending: dict = {}    # request id -> (mint, account, stage)
        routes: dict = {}     # subscription -> (mint, account, stage)
        refused: dict = {}    # mint -> time
        synced = 0.0
        while self.running:
            now = time.time()
            if now - synced >= 1:
                synced = now
                wanted = {t['mint']: (t['account'], t['stage']) for t in self.db.subscriptions()}
                for mint in list(active):
                    account, stage, sub = active[mint]
                    if wanted.get(mint) != (account, stage):
                        del active[mint]
                        routes.pop(sub, None)
                        request += 1
                        await ws.send_json({'jsonrpc': '2.0', 'id': request,
                                            'method': 'logsUnsubscribe', 'params': [sub]})
                waiting = {p[0] for p in pending.values()}
                for mint, (account, stage) in wanted.items():
                    if mint in active or mint in waiting or now - refused.get(mint, 0) < 60:
                        continue
                    request += 1
                    pending[request] = (mint, account, stage)
                    await ws.send_json({'jsonrpc': '2.0', 'id': request, 'method': 'logsSubscribe',
                                        'params': [{'mentions': [account]}, {'commitment': 'confirmed'}]})
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=0.20)
            except asyncio.TimeoutError:
                continue
            if msg.type != aiohttp.WSMsgType.TEXT:
                return
            try:
                body = json.loads(msg.data)
            except ValueError:
                continue
            if not isinstance(body, dict):
                continue
            if body.get('id') in pending:
                mint, account, stage = pending.pop(body['id'])
                sub = body.get('result')
                if isinstance(sub, int) and not isinstance(sub, bool):
                    active[mint] = (account, stage, sub)
                    routes[sub] = (mint, account, stage)
                else:
                    refused[mint] = time.time()
                    self.db.metric('ws_subscription_refused')
                    LOG.warning('logs subscription refused for %s', mint[:12])
                continue
            if body.get('method') != 'logsNotification':
                continue
            params = body.get('params') or {}
            route = routes.get(params.get('subscription'))
            result = params.get('result') or {}
            value = result.get('value') or {}
            signature = value.get('signature')
            if route and value.get('err') is None and isinstance(signature, str):
                self.on_logs(*route, signature, value.get('logs'), (result.get('context') or {}).get('slot'))

    def on_ws_restored(self):
        down = self.ws_down_at
        for outcome in self.db.open_outcomes():
            if down and outcome['call_at'] < down:
                self.db.set_outcome(outcome['mint'], gap=1)
                self.refresh.add(outcome['mint'])
        for token in self.db.watching():
            self.request_backfill(token['mint'])

    def on_logs(self, mint: str, account: str, stage: str, signature: str, logs, slot):
        if self.db.seen(mint, signature):
            return
        trades, need_tx = trades_from_logs(logs, mint, account, stage, slot)
        if trades:
            self.observe_clock(trades)
            self.ingest(mint, signature, trades)
            self.db.metric('trade_from_logs')
        elif need_tx:
            self.queue_tx(mint, signature)
        else:
            self.db.mark(mint, signature)

    def observe_clock(self, trades: list[Trade]):
        """Trades are judged in chain time: learn how far this device's clock is from it."""
        arrived = time.time()
        self.lags.extend(t.at - arrived for t in trades)
        offset = self.clock_offset()
        if offset and arrived - self.clock_warned > 3600:
            self.clock_warned = arrived
            LOG.warning('Device clock differs %.0f s from the blockchain clock; turn on automatic '
                        'time in Android settings (Solter corrects for it meanwhile)', -offset)

    def clock_offset(self) -> float:
        """Chain clock minus device clock. A trade only ever arrives after it happened, so delay
        lowers each sample; the highest sample is the closest to the true difference. Small
        differences (normal latency, whole-second block times) are left alone."""
        if len(self.lags) < 10:
            return 0.0
        offset = max(self.lags)
        return offset if abs(offset) > 5 else 0.0

    def chain_now(self, now: float | None = None) -> float:
        return (time.time() if now is None else now) + self.clock_offset()

    def ingest(self, mint: str, signature: str, trades: list[Trade]):
        now = time.time()
        self.db.mark(mint, signature, now)
        token = self.db.token(mint)
        if not token:
            return
        if token['state'] == 'watching':
            added = self.db.add_trades(mint, signature, trades)
            if added:
                # Direct dispatch: no 0.75-second evaluator tick, batch wait or
                # cross-token queue between decoding and flow evaluation.
                self.kick_evaluation(mint)
            if added and any(t.side == 'buy' and (t.sol or 0) >= self.rules.dust_sol for t in trades):
                # Real buying keeps the slot a little longer, up to three watch periods.
                cap = (token['watch_started'] or now) + 3 * self.cfg.watch_seconds
                until = min(cap, max(token['watch_until'] or 0, now + 60))
                if until > (token['watch_until'] or 0):
                    self.db.update(mint, watch_until=until)
        elif token['state'] == 'called':
            for t in trades:
                if t.price:
                    self.db.track(mint, t.price, t.at)
                if token['creator'] and t.wallet == token['creator'] and t.side == 'sell':
                    self.db.set_outcome(mint, dev_sold=1)

    # transaction fetches -------------------------------------------------
    def queue_tx(self, mint: str, signature: str):
        key = (mint, signature)
        if key in self.queued:
            return
        if self.fallback.full():
            self.db.metric('fallback_dropped')
            return
        self.queued.add(key)
        self.fallback.put_nowait(key)

    async def transactions(self):
        """Fetch a transaction only when its logs showed a trade but no decodable event."""
        while self.running:
            mint, signature = await self.fallback.get()
            try:
                token = self.db.token(mint)
                if not token or token['state'] not in ('watching', 'called') or self.db.seen(mint, signature):
                    continue
                try:
                    tx = await self.rpc.call('getTransaction', [signature, TX_OPTIONS], kind=FALLBACK)
                except NoBudget:
                    self.db.metric('fallback_no_budget')
                    continue
                if tx is None:
                    continue
                trades = trades_from_tx(tx, mint, token['account'], token['stage'])
                if trades:
                    self.ingest(mint, signature, trades)
                    self.db.metric(f'trade_from_{trades[0].src}')
                else:
                    self.db.mark(mint, signature)
            except Exception:
                LOG.exception('transaction fallback failed')
            finally:
                self.queued.discard((mint, signature))
                self.fallback.task_done()

    def request_backfill(self, mint: str):
        if self.cfg.backfill_tx and mint not in self.backfill_queued and not self.backfills.full():
            self.backfill_queued.add(mint)
            self.backfills.put_nowait(mint)

    async def backfill(self):
        """Recent trades of a newly watched token (or after an outage), so the window fills sooner."""
        while self.running:
            mint = await self.backfills.get()
            try:
                await self.backfill_one(mint)
            except NoBudget:
                self.db.metric('backfill_no_budget')
            except Exception:
                LOG.exception('backfill failed')
            finally:
                self.backfill_queued.discard(mint)
                self.backfills.task_done()

    async def backfill_one(self, mint: str):
        token = self.db.token(mint)
        if not token or token['state'] != 'watching' or not token['account']:
            return
        # Do not use last_sig as an `until` cursor: it belongs to the mint,
        # not necessarily to the currently watched account. Migrations and
        # provider pruning can make it unknown to getSignaturesForAddress,
        # which is RPC error -32020 on Agave 4.x. The processed table already
        # provides reliable deduplication without a pagination cursor.
        # Fetch more *signatures* than transactions: most may be seen/failed/old.
        # Only the configured number of recent, unseen transactions is fetched.
        options = {'limit': min(100, max(12, self.cfg.backfill_tx * 6)),
                   'commitment': 'confirmed'}
        items = await self.rpc.call('getSignaturesForAddress', [token['account'], options], kind=BACKFILL)
        if not isinstance(items, list):
            return
        oldest = self.chain_now() - self.rules.window
        newest = []
        for item in items:  # prioritize the newest unseen signatures
            signature = item.get('signature') if isinstance(item, dict) else None
            if not isinstance(signature, str) or item.get('err') is not None or self.db.seen(mint, signature):
                continue
            block_time = item.get('blockTime')
            if isinstance(block_time, (int, float)) and block_time < oldest:
                continue
            newest.append(signature)
            if len(newest) >= self.cfg.backfill_tx:
                break
        # Ingest chronologically so a later price never gets replaced by an
        # older backfill price (even though selection favors the newest trades).
        for signature in reversed(newest):
            if self.db.seen(mint, signature):
                continue  # live logs may have arrived while selecting signatures
            tx = await self.rpc.call('getTransaction', [signature, TX_OPTIONS], kind=BACKFILL)
            if tx is None:
                continue
            trades = trades_from_tx(tx, mint, token['account'], token['stage'])
            if trades:
                self.ingest(mint, signature, trades)
                self.db.metric('trade_from_backfill')
            else:
                self.db.mark(mint, signature)

    # free HTTP sources ---------------------------------------------------
    async def http_json(self, url: str, limit: int = 2**21):
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as r:
                if r.status != 200:
                    return None
                body = bytearray()
                async for chunk in r.content.iter_chunked(65536):   # read() may return a partial body
                    body += chunk
                    if len(body) > limit:
                        return None
                return json.loads(bytes(body))
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, UnicodeDecodeError):
            return None

    async def dex(self, path: str):
        async with self.dex_lock:
            wait = self.cfg.dex_interval - (time.monotonic() - self.last_dex)
            if wait > 0:
                await asyncio.sleep(wait)
            self.last_dex = time.monotonic()
        return await self.http_json(f'{DEX}{path}')

    async def sol_price(self):
        while self.running:
            price = sol_usd_from(await self.dex(f'/tokens/v1/solana/{WSOL}'))
            if price:
                self.sol_usd = price
            await asyncio.sleep(60)

    async def pair(self, mint: str, max_age: float = 30) -> dict:
        cached = self.pairs.get(mint)
        if cached and time.time() - cached[0] < max_age:
            return cached[1]
        facts = pair_facts(pumpswap_pair(await self.dex(f'/token-pairs/v1/solana/{mint}'), mint))
        self.pairs[mint] = (time.time(), facts)
        return facts

    async def token_metadata(self, token: dict) -> dict | None:
        mint, uri = token['mint'], token.get('uri')
        if not uri:
            return None
        cached = self.metadata.get(mint)
        if cached and (cached[1] is not None or time.time() - cached[0] < METADATA_RETRY):
            return cached[1]
        if uri.startswith('ipfs://'):
            uri = 'https://ipfs.io/ipfs/' + uri[len('ipfs://'):]
        data = await self.http_json(uri, 65536)
        data = data if isinstance(data, dict) else None
        self.metadata[mint] = (time.time(), data)
        return data

    # Older-pool discovery and market snapshots --------------------------
    async def market_scanner(self):
        """Free feeds complement launch events; unavailable feeds never kill other workers."""
        while self.running:
            started = time.monotonic()
            try:
                await self.scan_market(time.time())
            except Exception:
                LOG.exception('market discovery failed')
                self.db.metric('market_scan_error')
            await asyncio.sleep(max(1, self.cfg.market_scan_seconds - (time.monotonic() - started)))

    async def scan_market(self, now: float):
        """Slow discovery: trending/new pools and listing feeds; all free HTTP."""
        if not self.cfg.market_enabled:
            return
        urls = (f'{GECKO}/networks/solana/trending_pools?duration=5m&include=base_token',
                f'{GECKO}/networks/solana/new_pools?include=base_token')
        # Independent sources must not wait for each other's network latency.
        pages = await asyncio.gather(*(self.http_json(url) for url in urls))
        discovered = []
        for data in pages:
            if data is None:
                self.db.metric('market_gecko_unavailable')
            discovered.extend(gecko_mints(data))
        profiles = await asyncio.gather(*(self.dex(endpoint) for endpoint in
            ('/token-profiles/latest/v1', '/token-boosts/top/v1')))
        for feed in profiles:
            discovered.extend(dex_list_mints(feed))
            for entry in (feed if isinstance(feed, list) else []):
                item = extract_profile_media(entry)
                if item:
                    mint, media = item
                    old = self.market_media.get(mint, (0, {}))[1]
                    self.market_media[mint] = (now, {
                        'header': media.get('header') or old.get('header'),
                        'icon': media.get('icon') or old.get('icon'),
                        'socials': {**old.get('socials', {}), **media.get('socials', {})}})
        if len(self.market_media) > 1200:
            self.market_media = dict(sorted(self.market_media.items(),
                                            key=lambda entry: entry[1][0], reverse=True)[:600])
        # Fair rotation ensures old tokens can be rediscovered after a revival.
        discovered.extend(self.db.market_candidates(90))
        mints = list(dict.fromkeys(discovered))[:120]
        for start in range(0, len(mints), 30):
            await self.market_batch(mints[start:start + 30])

    async def market_fast_scanner(self):
        """Fresh migration requests + recent hot pools, without RPC, polling, or a watch slot."""
        while self.running:
            self.market_fast_wakeup.clear()
            pending = list(self.market_urgent)[:90]
            self.market_urgent.difference_update(pending)
            hot = self.db.hot_market_candidates(time.time(), 60)
            mints = list(dict.fromkeys(pending + hot))[:90]
            for start in range(0, len(mints), 30):
                await self.market_batch(mints[start:start + 30])
            if self.market_urgent:
                # Drain a burst rather than sleeping 15s between backlog chunks.
                continue
            try:
                await asyncio.wait_for(self.market_fast_wakeup.wait(),
                                       timeout=self.cfg.market_fast_seconds)
            except asyncio.TimeoutError:
                pass

    async def market_batch(self, mints: list[str]):
        """One batched public DEX request; candidates are decided as each batch arrives."""
        if not mints:
            return
        payload = await self.dex('/tokens/v1/solana/' + ','.join(mints))
        if payload is None:
            self.db.metric('market_dex_unavailable')
            return
        now = time.time()
        pairs = by_mint(payload, set(mints))
        for mint, pair in pairs.items():
            try:
                pool = pair['pairAddress']
                if self.cfg.scanner_mode == 'market' and (pair.get('quoteToken') or {}).get('address') != WSOL:
                    self.db.metric('market_non_sol_quote')
                    continue
                if migration_pool(mint) != pool:
                    self.db.metric('market_noncanonical_pool')
                    # Without RPC, owner/vault verification is impossible.
                    # Market-only alerts require the deterministic migration pool.
                    if self.cfg.scanner_mode == 'market':
                        continue
                snapshot = market_snapshot(pair, now)
                if not snapshot:
                    self.db.metric('market_bad_snapshot')
                    continue
                facts = pair_facts(pair)
                if facts.get('address') == pool:
                    self.pairs[mint] = (now, facts)
                eligible = market_ready(snapshot, now, self.cfg.market_min_buys,
                                        self.cfg.market_min_volume_usd,
                                        self.cfg.min_liquidity_usd)
                created = self.db.update_market(mint, pool, snapshot, now, eligible)
                self.db.metric('market_discovered' if created else 'market_updated')
                if self.cfg.scanner_mode == 'market':
                    # Artwork/socials from data already fetched; no added network request.
                    cached = self.market_media.get(mint)
                    profile = cached[1] if cached and now - cached[0] <= 3600 else None
                    media = combine_media(pair, profile)
                    await self.consider_market(mint, now, facts, media)
                elif eligible:
                    self.scheduler_wakeup.set()
            except (TypeError, ValueError, KeyError):
                self.db.metric('market_bad_pair')

    async def consider_market(self, mint: str, now: float, facts: dict,
                              media: dict | None = None):
        if self.db.has_call(mint):
            return
        token = self.db.token(mint)
        if not token or token.get('stage') != 'migrated':
            return
        decision = evaluate_market(
            token, now, minimum_buys=self.cfg.market_signal_min_buys,
            minimum_volume=self.cfg.market_signal_min_volume_usd,
            minimum_liquidity=self.cfg.min_liquidity_usd,
            min_mcap=self.cfg.min_mcap_usd, max_mcap=self.cfg.max_mcap_usd,
            minimum_change=self.cfg.market_signal_min_change_pct,
            maximum_change=self.cfg.market_signal_max_change_pct,
            buy_sell_ratio=self.cfg.market_signal_buy_sell_ratio)
        if not decision.ok:
            self.note(mint, decision.reason)
            return
        self.db.metric('market_signal_confirmed')
        # Refresh once immediately before dispatch; no network calls between
        # the final decision and Telegram. API snapshots can still be cached.
        if not self.db.claim_call(mint, 'migrated', self.cfg.max_calls_per_hour, now):
            self.note(mint, 'hourly call cap')
            return
        media = media or {}
        text = market_alert_text(token, decision.strength, now, media.get('socials'))
        decision_started = time.monotonic()
        state, msg_id = await self.tg.call(text, mint, media.get('image'))
        delivery_ms = round((time.monotonic() - decision_started) * 1000)
        price = facts.get('price_sol')
        mcap = token['market_mcap_usd'] / self.sol_usd if self.sol_usd else None
        self.db.finish_call(mint, state, msg_id, {
            'symbol': token['symbol'], 'name': token['name'],
            'score': decision.strength, 'grade': 'M', 'price_sol': price,
            'mcap_sol': mcap, 'sol_usd': self.sol_usd,
            'buyers': None, 'net_sol': None,
            'note': 'MARKET SNAPSHOT ONLY - UNVERIFIED ON-CHAIN'})
        if state == 'sent' and isinstance(price, (int, float)) and price > 0:
            self.db.open_outcome(mint, price, now)
        self.db.metric('market_alert_sent' if state == 'sent' else f'market_alert_{state}')
        LOG.info('MARKET ALERT %s strength=%d delivery=%s telegram_ms=%d (no RPC verification)',
                 mint[:12], decision.strength, state, delivery_ms)

    # event-driven evaluation ---------------------------------------------
    def kick_evaluation(self, mint: str, trade: bool = True):
        """Dispatch a watched mint immediately. Coalesce repeated trades per mint.

        This is deliberately not an RPC work queue: the cheap flow decision
        executes independently on each token as soon as its event arrives.
        A new event during a slow deep check is replayed after that check.
        """
        self.eval_revision[mint] = self.eval_revision.get(mint, 0) + 1
        if trade:
            self.event_received[mint] = time.monotonic()
        self.eval_wakeup.set()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # synchronous tests / offline parsing: evaluator is fallback
        if mint in self.eval_jobs:
            return
        token = self.db.token(mint)
        if not token or token['state'] != 'watching' or self.db.has_call(mint):
            return
        self.eval_jobs[mint] = loop.create_task(self._evaluate_one(token))

    async def _metadata_background(self, token: dict):
        try:
            async with self.metadata_slots:
                await self.token_metadata(token)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.db.metric('metadata_prefetch_failed')
        finally:
            self.metadata_jobs.pop(token['mint'], None)

    def prefetch_metadata(self, token: dict):
        """Optional socials/image never hold up the decision RPC."""
        mint = token['mint']
        if not token.get('uri') or mint in self.metadata_jobs or mint in self.metadata:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.metadata_jobs[mint] = loop.create_task(self._metadata_background(token))

    async def _evaluate_one(self, token: dict):
        mint = token['mint']
        try:
            while self.running:
                rev = self.eval_revision.get(mint, 0)
                latest = self.db.token(mint) if hasattr(self.db, 'token') else token
                if not latest or (latest.get('state') not in (None, 'watching')) or (
                        hasattr(self.db, 'has_call') and self.db.has_call(mint)):
                    return
                try:
                    await self.consider(latest, self.chain_now())
                except NoBudget:
                    self.db.metric('evaluation_no_budget')
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOG.exception('evaluation failed for %s', mint[:12])
                    return
                if rev == self.eval_revision.get(mint, 0):
                    return
                # Events arriving while an RPC was pending must not be missed.
                self.db.metric('evaluation_event_replayed')
                await asyncio.sleep(0)
        finally:
            self.eval_jobs.pop(mint, None)

    async def evaluator(self):
        # Dispatch independently per mint. The old gather() barrier let a single
        # 15s RPC timeout hold up an entire watch list (and every new trade).
        try:
            while self.running:
                self.eval_wakeup.clear()
                now = self.chain_now()
                tokens = self.db.watching()
                # Newest traded first; scheduled checks for quiet tokens still occur.
                tokens.sort(key=lambda t: t['last_trade_at'] or 0, reverse=True)
                for token in tokens:
                    mint = token['mint']
                    if mint in self.eval_jobs:
                        continue
                    last_at, last_trade = self.eval_last.get(mint, (0.0, None))
                    trade_at = token['last_trade_at']
                    if trade_at == last_trade and now - last_at < 2:
                        continue
                    self.eval_last[mint] = (now, trade_at)
                    self.eval_revision[mint] = self.eval_revision.get(mint, 0) + 1
                    self.eval_jobs[mint] = asyncio.create_task(self._evaluate_one(token))
                # New trades trigger immediate reevaluation. The timeout handles
                # flow aging and tokens whose initial subscriptions are still quiet.
                try:
                    await asyncio.wait_for(self.eval_wakeup.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
        finally:
            jobs = list(self.eval_jobs.values())
            for job in jobs:
                job.cancel()
            if jobs:
                await asyncio.gather(*jobs, return_exceptions=True)
            metadata_jobs = list(self.metadata_jobs.values())
            for job in metadata_jobs:
                job.cancel()
            if metadata_jobs:
                await asyncio.gather(*metadata_jobs, return_exceptions=True)

    def note(self, mint: str, reason: str):
        """Remember why a watched token was not called (once per change of reason)."""
        if self.reasons.get(mint) != reason:
            self.reasons[mint] = reason
            self.db.metric(f'no_call:{reason}')
            self.db.update(mint, reason=reason)

    async def consider(self, token: dict, now: float):
        mint = token['mint']
        if self.db.has_call(mint):
            return
        trades = self.db.trades(mint)
        flow = evaluate(trades, now, self.rules, token['creator'])
        if not flow.ok:
            self.note(mint, flow.reason)
            return
        if mint not in self.flow_qualified:
            self.flow_qualified.add(mint)
            self.db.metric('flow_confirmed')
            LOG.info('FLOW %s net=%.2f SOL buyers=%d: safety verification',
                     mint[:12], flow.net_sol, flow.buyers)
        # The market-cap window is checked on the last traded price first, before any RPC is spent,
        # and again on the authoritative price the deep check reads.
        last = token['last_price'] * PUMP_SUPPLY if token['last_price'] else None
        outside = market_cap_reason(self.quality, last, self.sol_usd)
        if outside:
            self.note(mint, outside)
            return
        # Checks for separate mints run concurrently, never behind a global
        # four-token decision lane. Provider request limits still apply.
        async with self.eval_rpc_slots:
            assessment = await self.assess(token, flow, trades, now)
        if assessment.reason:
            self.note(mint, assessment.reason)
            return
        outside = market_cap_reason(self.quality, assessment.mcap_sol, self.sol_usd)
        if outside:
            self.note(mint, outside)
            return
        # Re-check on *fresh* flow after the awaits. An expired surge must not
        # turn into a late call just because the safety RPC finished eventually.
        latest = self.db.token(mint)
        if not latest or latest['state'] != 'watching':
            return
        flow = evaluate(self.db.trades(mint), self.chain_now(), self.rules, latest['creator'])
        if not flow.ok:
            self.note(mint, flow.reason)
            self.db.metric('signal_expired_while_verifying')
            return
        assessment = replace(assessment, score=score(flow, assessment.holders,
                                                     assessment.socials, assessment.launches))
        if assessment.score < self.quality.min_score:
            self.note(mint, 'low score')
            return
        if mint not in self.quality_qualified:
            self.quality_qualified.add(mint)
            self.db.metric('quality_confirmed')
        await self.send_call(token, flow, assessment, now)

    async def assess(self, token: dict, flow: Flow, trades: list[Trade], now: float) -> Assessment:
        cached = self.checked.get(token['mint'])
        if cached:
            # A temporary RPC outage (missing holder data) must not block a
            # legitimate 3-minute momentum window for two full minutes.
            # Still do not issue a call without verified holder distribution.
            ttl = (HOLDER_RETRY_TTL if cached[1].reason == 'holders unavailable' else
                   FAILED_CHECK_TTL if cached[1].reason else CHECK_TTL)
        else:
            ttl = 0
        if cached and now - cached[0] < ttl:
            base = cached[1]
        else:
            base = await self.deep_check(token, trades)
            self.checked[token['mint']] = (now, base)
        if base.reason:
            return base
        return replace(base, score=score(flow, base.holders, base.socials, base.launches))

    async def account(self, address: str, encoding: str) -> dict | None:
        result = await self.rpc.call('getAccountInfo', [address, {'encoding': encoding,
                                                                   'commitment': 'confirmed'}], kind=DECISION)
        value = result.get('value') if isinstance(result, dict) else None
        return value if isinstance(value, dict) else None

    async def deep_check(self, token: dict, trades: list[Trade]) -> Assessment:
        mint = token['mint']
        mint_info, market_info = await asyncio.gather(
            self.account(mint, 'jsonParsed'), self.account(token['account'], 'base64'))
        safe, note = mint_safety(mint_info)
        if self.cfg.require_mint_check and not safe:
            return Assessment(note)
        token_program = mint_info.get('owner') if mint_info else TOKEN_PROGRAM
        supply = mint_supply(mint_info) or PUMP_SUPPLY * TOKEN_UNIT
        creator, liquidity, image, progress = token['creator'], None, None, None
        if token['stage'] == 'migrated':
            pool = pool_account(market_info)
            if not pool or pool.base_mint != mint or pool.quote_mint != WSOL:
                facts = await self.pair(mint)
                if facts.get('address') and facts['address'] != token['account']:
                    self.db.update(mint, account=facts['address'])
                    self.db.metric('pool_from_dexscreener')
                return Assessment('pool unverified')
            facts = await self.pair(mint)
            liquidity = facts.get('liquidity_usd')
            if not liquidity or liquidity < self.cfg.min_liquidity_usd:
                return Assessment('thin liquidity')
            if not token['symbol'] and facts.get('symbol'):
                self.db.update(mint, symbol=facts['symbol'], name=facts.get('name') or '')
            price = token['last_price'] or facts.get('price_sol')
            mcap = price * supply / TOKEN_UNIT if price else None
            image = facts.get('image')
            creator = creator or pool.coin_creator
            vault = pool.base_vault
        else:
            curve = curve_account(market_info)
            if not curve:
                return Assessment('curve unreadable')
            if curve.complete:
                return Assessment('curve complete')
            if curve.mayhem:
                return Assessment('mayhem mode')
            if not curve.sol_quoted:
                return Assessment('non-SOL quote')
            progress = curve.progress * 100
            if progress > self.quality.max_progress:
                return Assessment('late on curve')
            if curve.creator != token['creator']:
                self.db.update(mint, creator=curve.creator)   # the curve is the authority
                creator = curve.creator
                if any(t.wallet == creator and t.side == 'sell' for t in trades):
                    return Assessment('creator sold')
            price, mcap = curve.price, curve.mcap_sol
            vault = associated_account(token['account'], mint, token_program)
        if not price:
            return Assessment('price unknown')
        tasks = [self.rpc.call('getTokenLargestAccounts', [mint, {'commitment': 'confirmed'}],
                               kind=DECISION)]
        if creator:
            tasks.append(self.dev_balance(creator, mint))
        # Metadata/social images cannot delay a default decision. It is
        # prefetched in the background on watch promotion, and reused if ready.
        # Only an explicit REQUIRE_SOCIALS=1 makes fetching it a hard gate.
        if self.cfg.require_socials:
            tasks.append(asyncio.wait_for(self.token_metadata(token), timeout=2.5))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        largest = results[0] if isinstance(results[0], dict) else None
        rows = largest.get('value') if isinstance(largest, dict) else None
        if not isinstance(rows, list):
            self.db.metric('holders_rpc_unavailable')
            return Assessment('holders unavailable')
        dev_raw = results[1] if creator and isinstance(results[1], int) else None
        holders = holder_stats(rows, {vault}, supply, dev_raw)
        launches = self.db.launches(creator)
        meta = (results[-1] if self.cfg.require_socials and isinstance(results[-1], dict)
                else (self.metadata.get(mint) or (None, None))[1])
        socials = socials_from(meta)
        dev_buy = dev_buy_pct(token)
        reason = quality_reason(self.quality, holders, socials, launches, dev_buy)
        return Assessment(reason, price, mcap, progress, holders, socials, launches, dev_buy, 0,
                          liquidity, creator, image or socials.image)

    async def dev_balance(self, owner: str, mint: str) -> int | None:
        result = await self.rpc.call('getTokenAccountsByOwner', [
            owner, {'mint': mint}, {'encoding': 'jsonParsed', 'commitment': 'confirmed'}], kind=DECISION)
        accounts = result.get('value') if isinstance(result, dict) else None
        if not isinstance(accounts, list):
            return None
        total = 0
        for entry in accounts:
            try:
                total += int(entry['account']['data']['parsed']['info']['tokenAmount']['amount'])
            except (KeyError, TypeError, ValueError):
                return None
        return total

    async def send_call(self, token: dict, flow: Flow, a: Assessment, now: float):
        mint = token['mint']
        if not self.db.claim_call(mint, token['stage'], self.cfg.max_calls_per_hour, now):
            self.note(mint, 'hourly call cap')
            return
        text = call_text(token, flow, a, self.sol_usd, now, self.rules.window)
        state, message_id = await self.tg.call(text, mint, a.image)
        self.db.finish_call(mint, state, message_id, {
            'symbol': token['symbol'], 'name': token['name'], 'score': a.score,
            'grade': 'A' if a.score >= 75 else 'B' if a.score >= 60 else 'C',
            'price_sol': a.price_sol, 'mcap_sol': a.mcap_sol, 'sol_usd': self.sol_usd,
            'progress': a.progress, 'net_sol': flow.net_sol, 'buyers': flow.buyers,
            'top10': a.holders.top10 if a.holders else None,
            'dev_pct': a.holders.dev if a.holders else None,
            'launches': a.launches, 'socials': a.socials.count, 'note': 'single attempt'})
        self.db.open_outcome(mint, a.price_sol, now)
        self.db.metric('call_sent' if state == 'sent' else f'call_{state}')
        since_event = self.event_received.get(mint)
        latency_ms = round((time.monotonic() - since_event) * 1000) if since_event else None
        LOG.info('CALL %s %s score=%d delivery=%s event_to_delivery_ms=%s',
                 token['stage'], mint[:12], a.score, state, latency_ms)

    # after the call ------------------------------------------------------
    async def tracker(self):
        while self.running:
            now = time.time()
            for outcome in self.db.open_outcomes():
                try:
                    if self.cfg.scanner_mode == 'market' and outcome['stage'] != 'migrated':
                        # Old bonding-curve calls cannot be followed without RPC.
                        self.db.set_outcome(outcome['mint'], gap=1, done=1)
                        continue
                    await self.follow(outcome, now)
                except NoBudget:
                    break
                except Exception:
                    LOG.exception('tracking failed for %s', outcome['mint'][:12])
            await asyncio.sleep(15)

    async def current_price(self, outcome: dict) -> float | None:
        if outcome['stage'] == 'migrated' or outcome.get('token_stage') == 'migrated':
            return (await self.pair(outcome['mint'], max_age=10)).get('price_sol')
        curve = curve_account(await self.account(outcome['account'], 'base64'))
        return curve.price if curve else None

    async def follow(self, o: dict, now: float):
        mint = o['mint']
        stale = (o.get('token_stage') == 'migrated' or o['stage'] == 'migrated') and now - o['last_at'] > 60
        if mint in self.refresh or stale:
            self.refresh.discard(mint)
            price = await self.current_price(o)
            if price:
                self.db.track(mint, price, now)
                o = {**o, **(self.db.outcome(mint) or {})}
        elapsed = (now - o['call_at']) / 60
        updates = {}
        for h in HORIZONS:
            if o[f'r{h}'] is None and elapsed >= h and h <= self.cfg.track_minutes:
                updates[f'r{h}'] = o['last'] / o['entry']
        multiple = o['peak'] / o['entry']
        reached = max((m for m in MILESTONES if multiple >= m), default=0)
        if reached > o['milestone']:
            updates['milestone'] = reached
            if self.cfg.followups and o['message_id']:
                # Market-mode signals have no verified token supply; never
                # invent a market cap by multiplying price by PUMP_SUPPLY.
                milestone_mcap = None if o.get('grade') == 'M' else o['peak'] * PUMP_SUPPLY
                await self.tg.reply(milestone_text(o['symbol'], multiple, milestone_mcap,
                                                   self.sol_usd, elapsed), o['message_id'])
        if o['dev_sold'] and not o['dev_alerted']:
            updates['dev_alerted'] = 1
            if self.cfg.followups and o['message_id']:
                await self.tg.reply(dev_sold_text(o['symbol']), o['message_id'])
        if elapsed >= self.cfg.track_minutes:
            updates['done'] = 1
        if updates:
            self.db.set_outcome(mint, **updates)

    # upkeep --------------------------------------------------------------
    async def maintenance(self):
        last_log, before = time.time(), self.db.metrics()
        while self.running:
            now = time.time()
            self.db.expire(now, self.cfg.reservoir_minutes)
            self.db.cleanup(now)
            for cache, ttl in ((self.checked, 300), (self.metadata, 3600), (self.pairs, 600)):
                for key in [k for k, v in cache.items() if now - v[0] > ttl]:
                    del cache[key]
            if len(self.reasons) > 5000:
                self.reasons.clear()
            # Long-running Termux sessions may see tens of thousands of tokens.
            # Keep direct-dispatch bookkeeping only for active/in-flight mints.
            active_mints = {t['mint'] for t in self.db.watching()} | set(self.eval_jobs)
            for book in (self.eval_revision, self.event_received, self.eval_last):
                if len(book) > 2000:
                    for mint in list(book):
                        if mint not in active_mints:
                            book.pop(mint, None)
            if len(self.flow_qualified) > 10000:
                self.flow_qualified.clear()
                self.quality_qualified.clear()
            if now - last_log >= 300:
                last_log = now
                after, s = self.db.metrics(), self.db.stats()
                text, warnings = pulse(before, after, s['tokens'].get('watching', 0),
                                       market_mode=self.cfg.scanner_mode == 'market')
                before = after
                LOG.info('STATUS 5 mnt: %s | RPC %d · SOL %s · mode %s', text, s['rpc_today'],
                         f'${self.sol_usd:.0f}' if self.sol_usd else 'n/a', self.cfg.scanner_mode)
                for warning in warnings:
                    LOG.warning('CEK: %s', warning)
            await asyncio.sleep(60)

    async def supervise(self, name: str, worker):
        """Restart a long-lived worker that fails instead of stopping the bot."""
        backoff = 3
        while self.running:
            try:
                await worker()
                if self.running:
                    LOG.warning('worker %s returned unexpectedly', name)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception('worker %s crashed, restarting', name)
                self.db.metric('worker_restart')
            if not self.running:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    def workers_for_mode(self):
        if self.cfg.scanner_mode == 'market':
            # Zero Solana RPC requests or WebSocket subscriptions while running.
            return (self.discover, self.market_scanner, self.market_fast_scanner,
                    self.sol_price, self.tracker, self.maintenance)
        workers = (self.discover, self.sweep, self.scheduler, self.logs, self.transactions,
                   self.backfill, self.sol_price, self.evaluator, self.tracker, self.maintenance)
        return workers + ((self.market_scanner,) if self.cfg.market_enabled else ())

    async def run(self):
        if self.cfg.scanner_mode == 'market' and not self.cfg.market_enabled:
            raise ValueError('MARKET_ENABLED must be true when SCANNER_MODE=market')
        if not self.cfg.tg_token or not self.cfg.tg_chat:
            raise ValueError('TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required')
        async with aiohttp.ClientSession(headers={'User-Agent': f'Solter/{__version__}'}) as session:
            self.attach(session)
            LOG.info('SOLTER v%s: mode=%s, market-only makes ZERO Solana RPC calls',
                     __version__, self.cfg.scanner_mode)
            workers = self.workers_for_mode()
            jobs = [asyncio.create_task(self.supervise(w.__name__, w)) for w in workers]
            try:
                await asyncio.gather(*jobs)
            finally:
                self.running = False
                for job in jobs:
                    job.cancel()
                await asyncio.gather(*jobs, return_exceptions=True)


async def probe(cfg: Config, store: Store, samples: int = 5) -> dict:
    """Diagnose the selected mode, without needlessly using an RPC in market mode."""
    if cfg.scanner_mode == 'market':
        async with aiohttp.ClientSession(headers={'User-Agent': f'Solter/{__version__}'}) as session:
            engine = Solter(cfg, store)
            engine.attach(session)
            trending, latest, wsol = await asyncio.gather(
                engine.http_json(f'{GECKO}/networks/solana/trending_pools?duration=5m&include=base_token'),
                engine.dex('/token-profiles/latest/v1'),
                engine.dex(f'/tokens/v1/solana/{WSOL}'))
            return {'scanner_mode': 'market', 'solana_rpc_requests': 0,
                    'gecko_available': isinstance(trending, dict),
                    'gecko_sample_mints': len(gecko_mints(trending)),
                    'dex_profiles_available': isinstance(latest, list),
                    'dex_wsol_available': isinstance(wsol, list),
                    'sol_usd': sol_usd_from(wsol)}
    out: dict = {}
    async with aiohttp.ClientSession(headers={'User-Agent': f'Solter/{__version__}'}) as session:
        rpc = BoundedRPC(cfg.rpc_http, store, cfg.rpc_per_minute, cfg.rpc_per_day, session)
        found = {'from_logs': 0, 'from_inner_instructions': 0, 'none': 0}
        sigs = await rpc.call('getSignaturesForAddress', [PUMP_PROGRAM, {'limit': 20}]) or []
        example = None
        for item in [s for s in sigs if isinstance(s, dict) and s.get('err') is None][:samples]:
            tx = await rpc.call('getTransaction', [item['signature'], TX_OPTIONS])
            if not isinstance(tx, dict):
                continue
            logs = (tx.get('meta') or {}).get('logMessages')
            events = [e for e in map(decode_pump_trade, log_payloads(logs, PUMP_PROGRAM)[0]) if e]
            if events:
                found['from_logs'] += 1
                example = example or events[0]
            elif [e for e in map(decode_pump_trade, cpi_payloads(tx, PUMP_PROGRAM)) if e]:
                found['from_inner_instructions'] += 1
            else:
                found['none'] += 1
        out['pump_trade_events'] = found
        if example:
            out['example_trade'] = {'mint': example['mint'], 'side': 'buy' if example['is_buy'] else 'sell',
                                    'sol': example['sol'] / 1e9, 'user': example['user']}
        pools = {'checked': 0, 'canonical_match': 0}
        sigs = await rpc.call('getSignaturesForAddress', [PUMP_AMM, {'limit': 10}]) or []
        for item in [s for s in sigs if isinstance(s, dict) and s.get('err') is None][:3]:
            tx = await rpc.call('getTransaction', [item['signature'], TX_OPTIONS])
            logs = ((tx or {}).get('meta') or {}).get('logMessages')
            events = [e for e in map(decode_amm_trade, log_payloads(logs, PUMP_AMM)[0]) if e]
            if not events:
                continue
            result = await rpc.call('getAccountInfo', [events[0]['pool'], {'encoding': 'base64'}])
            pool = pool_account(result.get('value') if isinstance(result, dict) else None)
            if pool and pool.quote_mint == WSOL:
                pools['checked'] += 1
                pools['canonical_match'] += migration_pool(pool.base_mint) == events[0]['pool']
        out['pumpswap_pools'] = pools
        sol = sol_usd_from(await _json(session, f'{DEX}/tokens/v1/solana/{WSOL}'))
        out['sol_usd_dexscreener'] = sol
        try:
            portal_url = PUMP_WS + ('?api-key=' + quote(cfg.portal_key, safe='')
                                      if cfg.portal_key else '')
            async with session.ws_connect(portal_url, timeout=15) as ws:
                await ws.send_json({'method': 'subscribeNewToken'})
                launch = None
                deadline = time.time() + 30
                while time.time() < deadline and not launch:
                    msg = await asyncio.wait_for(ws.receive(), timeout=max(1, deadline - time.time()))
                    parsed = parse_portal(msg.data) if msg.type == aiohttp.WSMsgType.TEXT else None
                    if parsed and parsed[0] == 'create':
                        launch = parsed[1]
                out['pumpportal_launch'] = launch
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            out['pumpportal_launch'] = f'error: {type(exc).__name__}'
        out['rpc_requests_used'] = store.budget_used()
    return out


async def _json(session, url: str):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as r:
            return await r.json(content_type=None) if r.status == 200 else None
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None
