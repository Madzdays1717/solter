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
from .sources import DEX, PUMP_WS, pair_facts, parse_portal, pumpswap_pair, sol_usd_from
from .report import pulse
from .storage import HORIZONS, Store
from .telegram import Telegram, call_text, dev_sold_text, milestone_text

LOG = logging.getLogger('solter')
TX_OPTIONS = {'encoding': 'jsonParsed', 'commitment': 'confirmed', 'maxSupportedTransactionVersion': 1}
MILESTONES = (2, 3, 5, 10)
METADATA_RETRY = 300    # a metadata fetch that failed is tried again after this
CHECK_TTL = 45          # seconds a passing deep check is reused
FAILED_CHECK_TTL = 120  # a failing one changes slowly (holders, dev): look again less often
COOLDOWN = 300          # seconds before a demoted token may be watched again


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
        self.dex_lock = asyncio.Lock()
        self.last_dex = 0.0
        self.lags: deque = deque(maxlen=200)    # event timestamp minus arrival time, live trades
        self.clock_warned = 0.0

    def attach(self, session):
        self.session = session
        self.rpc = BoundedRPC(self.cfg.rpc_http, self.db, self.cfg.rpc_per_minute,
                              self.cfg.rpc_per_day, session)
        self.tg = Telegram(self.cfg.tg_token, self.cfg.tg_chat, session)

    # discovery -----------------------------------------------------------
    async def discover(self):
        backoff = 2
        while self.running:
            try:
                async with self.session.ws_connect(PUMP_WS, heartbeat=25, timeout=20,
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

    def on_migration(self, mint: str, now: float):
        try:
            pool = migration_pool(mint)
        except ValueError:
            return
        was = self.db.migrate(mint, pool, now)
        self.db.metric('migration_seen')
        if was == 'watching':
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
            for token, account in zip(chunk, values):
                updates = classify(token, curve_account(account), checked_at, self.reservoir)
                self.db.update(token['mint'], **updates)
                state = updates.get('state')
                if state and state != token['state']:
                    self.db.metric(f'reservoir_{state}')
        return requests

    # live watch slots ----------------------------------------------------
    async def scheduler(self):
        while self.running:
            try:
                self.schedule(time.time())
            except Exception:
                LOG.exception('scheduler failed')
            await asyncio.sleep(3)

    def schedule(self, now: float):
        for t in self.db.watching():
            started = t['watch_started'] or now
            idle = self.chain_now(now) - (t['last_trade_at'] or started)
            if now >= (t['watch_until'] or 0):
                self.db.demote(t['mint'], now, COOLDOWN, 'watched without a call')
                self.db.metric('watch_ended')
            elif now - started > 90 and idle > 90:
                self.db.demote(t['mint'], now, COOLDOWN, 'flow went quiet')
                self.db.metric('watch_quiet')
        active = self.db.watching()
        free = self.cfg.max_watch - len(active)
        if free <= 0:
            return
        migrated_cap = max(1, self.cfg.max_watch // 3)
        migrated = sum(1 for t in active if t['stage'] == 'migrated')
        for t in candidates(self.db.alive(), now, self.reservoir, self.db.launches):
            if free <= 0:
                break
            if self.db.has_call(t['mint']):
                self.db.update(t['mint'], state='called')
                continue
            if t['stage'] == 'migrated':
                if migrated >= migrated_cap:
                    continue
                migrated += 1
            if self.db.promote(t['mint'], now, self.cfg.watch_seconds):
                free -= 1
                self.db.metric('promoted')
                LOG.info('WATCH %s %s inflow %.2f SOL/min stage=%s', t['mint'][:12],
                         (t['symbol'] or '')[:16], t['traction'] or 0, t['stage'])
                self.request_backfill(t['mint'])

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
                msg = await asyncio.wait_for(ws.receive(), timeout=2)
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
            if self.db.add_trades(mint, signature, trades) and any(
                    t.side == 'buy' and (t.sol or 0) >= self.rules.dust_sol for t in trades):
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
        options = {'limit': self.cfg.backfill_tx, 'commitment': 'confirmed'}
        if token['last_sig']:
            options['until'] = token['last_sig']
        items = await self.rpc.call('getSignaturesForAddress', [token['account'], options], kind=BACKFILL)
        if not isinstance(items, list):
            return
        oldest = time.time() - self.rules.window
        for item in reversed(items):
            signature = item.get('signature') if isinstance(item, dict) else None
            if not isinstance(signature, str) or item.get('err') is not None or self.db.seen(mint, signature):
                continue
            if (item.get('blockTime') or oldest) < oldest:
                continue
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

    # evaluation ----------------------------------------------------------
    async def evaluator(self):
        while self.running:
            for token in self.db.watching():
                try:
                    await self.consider(token, self.chain_now())
                except NoBudget:
                    LOG.warning('RPC budget used up; deep checks wait for tomorrow (UTC)')
                    break
                except Exception:
                    LOG.exception('evaluation failed for %s', token['mint'][:12])
            await asyncio.sleep(4)

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
        # The market-cap window is checked on the last traded price first, before any RPC is spent,
        # and again on the authoritative price the deep check reads.
        last = token['last_price'] * PUMP_SUPPLY if token['last_price'] else None
        outside = market_cap_reason(self.quality, last, self.sol_usd)
        if outside:
            self.note(mint, outside)
            return
        assessment = await self.assess(token, flow, trades, now)
        if assessment.reason:
            self.note(mint, assessment.reason)
            return
        outside = market_cap_reason(self.quality, assessment.mcap_sol, self.sol_usd)
        if outside:
            self.note(mint, outside)
            return
        if assessment.score < self.quality.min_score:
            self.note(mint, 'low score')
            return
        await self.send_call(token, flow, assessment, now)

    async def assess(self, token: dict, flow: Flow, trades: list[Trade], now: float) -> Assessment:
        cached = self.checked.get(token['mint'])
        if cached and now - cached[0] < (FAILED_CHECK_TTL if cached[1].reason else CHECK_TTL):
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
        mint_info = await self.account(mint, 'jsonParsed')
        safe, note = mint_safety(mint_info)
        if self.cfg.require_mint_check and not safe:
            return Assessment(note)
        token_program = mint_info.get('owner') if mint_info else TOKEN_PROGRAM
        supply = mint_supply(mint_info) or PUMP_SUPPLY * TOKEN_UNIT
        creator, liquidity, image, progress = token['creator'], None, None, None
        if token['stage'] == 'migrated':
            pool = pool_account(await self.account(token['account'], 'base64'))
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
            curve = curve_account(await self.account(token['account'], 'base64'))
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
        largest = await self.rpc.call('getTokenLargestAccounts', [mint, {'commitment': 'confirmed'}],
                                      kind=DECISION)
        rows = largest.get('value') if isinstance(largest, dict) else None
        if not isinstance(rows, list):
            return Assessment('holders unavailable')
        dev_raw = await self.dev_balance(creator, mint) if creator else None
        holders = holder_stats(rows, {vault}, supply, dev_raw)
        launches = self.db.launches(creator)
        socials = socials_from(await self.token_metadata(token))
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
        LOG.info('CALL %s %s score=%d delivery=%s', token['stage'], mint[:12], a.score, state)

    # after the call ------------------------------------------------------
    async def tracker(self):
        while self.running:
            now = time.time()
            for outcome in self.db.open_outcomes():
                try:
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
                await self.tg.reply(milestone_text(o['symbol'], multiple, o['peak'] * PUMP_SUPPLY,
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
            if now - last_log >= 300:
                last_log = now
                after, s = self.db.metrics(), self.db.stats()
                text, warnings = pulse(before, after, s['tokens'].get('watching', 0))
                before = after
                LOG.info('STATUS 5 mnt: %s | RPC %d/%d · SOL %s · WS %s', text, s['rpc_today'],
                         self.cfg.rpc_per_day, f'${self.sol_usd:.0f}' if self.sol_usd else 'n/a',
                         'up' if self.ws_up else 'DOWN')
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

    async def run(self):
        if not self.cfg.tg_token or not self.cfg.tg_chat:
            raise ValueError('TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required')
        async with aiohttp.ClientSession(headers={'User-Agent': f'Solter/{__version__}'}) as session:
            self.attach(session)
            LOG.info('SOLTER v%s: watch=%d RPC cap=%d/day %d/min, free sources only',
                     __version__, self.cfg.max_watch, self.cfg.rpc_per_day, self.cfg.rpc_per_minute)
            workers = (self.discover, self.sweep, self.scheduler, self.logs, self.transactions,
                       self.backfill, self.sol_price, self.evaluator, self.tracker, self.maintenance)
            jobs = [asyncio.create_task(self.supervise(w.__name__, w)) for w in workers]
            try:
                await asyncio.gather(*jobs)
            finally:
                self.running = False
                for job in jobs:
                    job.cancel()
                await asyncio.gather(*jobs, return_exceptions=True)


async def probe(cfg: Config, store: Store, samples: int = 5) -> dict:
    """Check the live assumptions on this machine: events in logs, pool derivation, free feeds."""
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
            async with session.ws_connect(PUMP_WS, timeout=15) as ws:
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
