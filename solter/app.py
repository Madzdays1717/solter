"""Run market intake, momentum analysis, and alert delivery."""

import asyncio
from collections import deque
from dataclasses import replace
import logging
import time

import aiohttp

from .config import Config
from .events import Event, number, parse_event
from .momentum import MAX_AGE, Token
from .metadata import parse_profile
from .market import MarketReader, MarketSnapshot
from .storage import Storage
from .stream import TradeStream
from .social import SocialMonitor
from .telegram import Telegram

LOG = logging.getLogger('solter')
COIN_API = 'https://frontend-api-v3.pump.fun/coins-v2/'
PROFILE_API = 'https://sol.shrine.trade/metadata'
SOL_PRICE_API = 'https://sol.shrine.trade/sol-price'


class App:
    def __init__(self, config: Config, storage: Storage):
        self.config = config
        self.storage = storage
        self.tokens = storage.load_launches()
        self.pending: set[str] = set()
        self.pending_events: dict[str, deque[Event]] = {}
        self.sol_usd = 0.0
        self.sol_price_updated_at = 0.0
        self.last_slot = storage.checkpoint()[0]
        self.last_flush = time.monotonic()
        self.seen: set[tuple[str, str, str, str]] = set()
        self.seen_order: deque[tuple[str, str, str, str]] = deque()
        self.changed: set[str] = set()
        self.notified: set[str] = set()
        self.profile_candidates: set[str] = set()
        self.profile_requested: set[str] = set()
        self.delivered: dict[str, tuple[object, float, int, str]] = {}
        self.lookup_attempts: dict[str, int] = {}
        self._reasons: dict[str, str] = {}
        self.alert_tasks: set[asyncio.Task] = set()
        self.verifying: set[str] = set()
        self.market: MarketReader | None = None
        self.social: SocialMonitor | None = None
        self.intake_count = 0
        self.call_count = 0
        self.stream = TradeStream(self.on_message, storage.checkpoint)
        self.session: aiohttp.ClientSession | None = None
        self.telegram: Telegram | None = None

    async def run(self):
        timeout = aiohttp.ClientTimeout(total=12, connect=6)
        async with aiohttp.ClientSession(timeout=timeout,
                headers={'User-Agent': 'Solter/3.5.0'}) as session:
            self.session = session
            self.market = MarketReader(session)
            self.social = SocialMonitor(session, self.on_social_report)
            self.telegram = Telegram(session, self.config.telegram_token,
                                     self.config.telegram_chat_id)
            jobs = [
                asyncio.create_task(self.stream.run(session), name='market-stream'),
                asyncio.create_task(self.housekeeping(), name='housekeeping'),
                asyncio.create_task(self.enrich_unknown(), name='metadata-1'),
                asyncio.create_task(self.enrich_unknown(), name='metadata-2'),
                asyncio.create_task(self.refresh_sol_price(), name='sol-price'),
                asyncio.create_task(self.social.run(), name='social-monitor'),
            ]
            LOG.info('Solter 3.5.0 running; cached launches=%d', len(self.tokens))
            try:
                await asyncio.gather(*jobs)
            finally:
                for job in jobs:
                    job.cancel()
                await asyncio.gather(*jobs, return_exceptions=True)
                self.flush()
                if self.alert_tasks:
                    await asyncio.wait(self.alert_tasks, timeout=8)

    async def refresh_sol_price(self):
        while True:
            if time.time() - self.sol_price_updated_at > 90:
                try:
                    async with self.session.get(
                        SOL_PRICE_API, timeout=aiohttp.ClientTimeout(total=4)
                    ) as response:
                        if response.status == 200:
                            data = await response.json(content_type=None)
                            price = number(data.get('priceUSD')) if isinstance(data, dict) else 0
                            if 1 < price < 2000:
                                self.sol_usd = price
                                self.sol_price_updated_at = time.time()
                        else:
                            LOG.debug('SOL price HTTP %d', response.status)
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                    LOG.debug('SOL price snapshot unavailable')
            await asyncio.sleep(45)

    def on_message(self, kind: str, data: object):
        if kind == 'sol_price':
            if isinstance(data, dict) and 1 < number(data.get('priceUSD')) < 2000:
                self.sol_usd = number(data['priceUSD'])
                self.sol_price_updated_at = time.time()
            return
        event = parse_event(data)
        if event is None:
            return
        self.intake_count += 1
        if event.slot:
            self.last_slot = max(self.last_slot, event.slot)
        key = (event.signature, event.mint, event.pool, event.action)
        if event.signature:
            if key in self.seen:
                return
            self.seen.add(key)
            self.seen_order.append(key)
            if len(self.seen_order) > 40000:
                self.seen.discard(self.seen_order.popleft())
        now = time.time()
        token = self.tokens.get(event.mint)
        if event.action == 'create':
            if now - event.timestamp > MAX_AGE:
                return
            if token is None:
                token = Token(mint=event.mint, launched_at=event.timestamp)
                self.tokens[event.mint] = token
            token.name = event.name or token.name
            token.symbol = event.symbol or token.symbol
            token.restricted |= event.restricted
            token.add(event, now)
            self.changed.add(event.mint)
            for waiting in self.pending_events.pop(event.mint, ()):
                self.process_trade(token, waiting, now)
            return
        if token is None:
            if event.action in ('buy', 'migrate', 'createPool'):
                if event.mint not in self.pending_events and len(self.pending_events) < 500:
                    self.pending_events[event.mint] = deque(maxlen=60)
                    self.pending.add(event.mint)
                if event.mint in self.pending_events and event.action in ('buy', 'sell'):
                    self.pending_events[event.mint].append(event)
            return
        if now - token.launched_at > MAX_AGE:
            return
        if event.action in ('migrate', 'createPool'):
            token.add(event, now)
            self.changed.add(event.mint)
        elif event.action in ('buy', 'sell'):
            self.process_trade(token, event, now)

    def process_trade(self, token: Token, event: Event, now: float):
        token.add(event, now)
        self.changed.add(token.mint)
        if event.action != 'buy' or now - event.timestamp > 20:
            return
        if token.mint in self.notified:
            return
        if len(token.observations) >= 3:
            self.queue_profile(token.mint)
            # Social data is supplementary. The scanner never awaits this work.
            buys = [e for e in token.observations if e.action == 'buy']
            if len(buys) >= 4 and sum(e.volume_sol for e in buys) >= .5:
                self.social.request(token.mint, priority=sum(e.volume_sol for e in buys))
        signal, reason = token.decide(now)
        if signal is None:
            # Keep the reason locally; persist it in the periodic flush.
            self.reasons[token.mint] = reason
            return
        if token.mint in self.verifying:
            return
        self.social.request(token.mint, priority=100 + signal.buy_sol)
        self.verifying.add(token.mint)
        task = asyncio.create_task(self.verify_and_deliver(token.mint))
        self.alert_tasks.add(task)
        task.add_done_callback(self.alert_tasks.discard)

    async def verify_and_deliver(self, mint: str):
        """Read optional market data within a deadline, then evaluate fresh trades."""
        try:
            token = self.tokens.get(mint)
            if token is None:
                return
            snapshot = await self.market.read(mint, token.phase)
            token = self.tokens.get(mint)
            if token is None:
                return
            signal, reason = token.decide(time.time())
            if signal is None:
                self.reasons[mint] = reason
                return
            if snapshot:
                # External snapshots confirm available data, but do not override
                # the chronology of the incoming live transaction stream.
                if snapshot.liquidity_usd and token.phase == 'PumpSwap' and snapshot.liquidity_usd < 3000:
                    self.reasons[mint] = 'liquidity too thin'
                    return
                if snapshot.buys_5m + snapshot.sells_5m >= 20 and snapshot.sells_5m > 3 * max(1, snapshot.buys_5m):
                    self.reasons[mint] = 'market sell pressure'
                    return
                signal = replace(
                    signal, reported_mc_usd=snapshot.market_cap_usd or signal.reported_mc_usd,
                    reported_mc_at=snapshot.observed_at if snapshot.market_cap_usd else signal.reported_mc_at,
                    image=snapshot.image or signal.image,
                    banner=snapshot.banner or signal.banner,
                    twitter=snapshot.twitter or signal.twitter,
                    telegram=snapshot.telegram or signal.telegram,
                    website=snapshot.website or signal.website,
                    liquidity_usd=snapshot.liquidity_usd,
                    market_source=snapshot.source,
                )
            if self.storage.already_alerted(mint):
                self.notified.add(mint)
                return
            if not self.storage.reserve(mint):
                self.notified.add(mint)
                return
            self.notified.add(mint)
            self.call_count += 1
            delay = time.time() - signal.timestamp
            LOG.info('SIGNAL %s age=%ds delay=%.2fs buy=%.2f SOL',
                     mint, int(signal.age_seconds), delay, signal.buy_sol)
            await self.deliver(signal)
        except Exception:
            LOG.exception('Signal evaluation failed for %s', mint)
        finally:
            self.verifying.discard(mint)

    @property
    def reasons(self):
        return self._reasons

    def queue_profile(self, mint: str):
        if mint not in self.profile_requested:
            self.profile_requested.add(mint)
            self.profile_candidates.add(mint)

    def on_social_report(self, mint: str, report):
        self.storage.save_social(mint, report)
        if mint in self.delivered:
            self.queue_alert_update(mint)

    def signal_with_profile(self, signal):
        token = self.tokens.get(signal.mint)
        if token is None:
            return signal
        report = self.social.reports.get(signal.mint) if self.social else None
        return replace(
            signal, name=token.name or signal.name,
            symbol=token.symbol or signal.symbol,
            image=token.image or signal.image, banner=token.banner or signal.banner,
            twitter=token.twitter or signal.twitter,
            telegram=token.telegram or signal.telegram,
            website=token.website or signal.website,
            reported_mc_usd=(token.reported_mc_usd if token.reported_mc_at > signal.reported_mc_at
                             else signal.reported_mc_usd),
            reported_mc_at=max(token.reported_mc_at, signal.reported_mc_at),
            social_status=report.status if report else signal.social_status,
            social_posts=report.posts if report else signal.social_posts,
            social_authors=report.authors if report else signal.social_authors,
            social_similarity=report.similar_pct if report else signal.social_similarity,
            social_topic=report.topic if report else signal.social_topic,
            social_observed_at=report.observed_at if report else signal.social_observed_at,
        )

    async def deliver(self, signal):
        sol_usd = self.sol_usd if time.time() - self.sol_price_updated_at <= 120 else 0.0
        signal = self.signal_with_profile(signal)
        try:
            state, message, kind = await self.telegram.send(signal, sol_usd)
        except Exception:
            LOG.exception('Alert delivery exception')
            state, message, kind = 'uncertain', None, 'text'
        self.storage.finish(signal.mint, state, message)
        if state == 'sent' and message:
            self.delivered[signal.mint] = (signal, sol_usd, message, kind)
            fresh = self.signal_with_profile(signal)
            if (fresh.twitter, fresh.telegram, fresh.website, fresh.banner, fresh.image, fresh.social_status, fresh.social_posts, fresh.social_authors, fresh.social_topic, fresh.social_similarity) != (
                    signal.twitter, signal.telegram, signal.website, signal.banner, signal.image, signal.social_status, signal.social_posts, signal.social_authors, signal.social_topic, signal.social_similarity):
                self.queue_alert_update(signal.mint)
        LOG.info('ALERT %s %s event_to_delivery_ms=%d', state, signal.mint,
                 max(0, int((time.time() - signal.timestamp) * 1000)))

    def queue_alert_update(self, mint: str):
        sent = self.delivered.get(mint)
        if not sent:
            return
        original, sol_usd, message_id, kind = sent
        updated = self.signal_with_profile(original)
        if (original.twitter, original.telegram, original.website, original.banner, original.image, original.social_status, original.social_posts, original.social_authors, original.social_topic, original.social_similarity) == (
                updated.twitter, updated.telegram, updated.website, updated.banner, updated.image, updated.social_status, updated.social_posts, updated.social_authors, updated.social_topic, updated.social_similarity):
            return
        self.delivered[mint] = (updated, sol_usd, message_id, kind)
        task = asyncio.create_task(self.telegram.update(message_id, updated, sol_usd,
                                                        kind=kind, previous=original))
        self.alert_tasks.add(task)
        task.add_done_callback(self.alert_tasks.discard)

    async def fetch_profile(self, mint: str):
        """Resolve metadata out of band; a network error never blocks a call."""
        primary = None
        fallback = None
        try:
            async with self.session.get(COIN_API + mint,
                    timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status == 200:
                    primary = parse_profile(await response.json(content_type=None), pump=True)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            pass
        if primary is None or not (primary.image and primary.banner and
                                   primary.twitter and primary.telegram and primary.website):
            try:
                async with self.session.get(PROFILE_API, params={'mint': mint},
                        timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        fallback = parse_profile(await response.json(content_type=None), pump=False)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                pass
        if primary is None:
            return fallback
        if fallback is None:
            return primary
        return replace(primary,
            image=primary.image or fallback.image,
            banner=primary.banner or fallback.banner,
            twitter=primary.twitter or fallback.twitter,
            telegram=primary.telegram or fallback.telegram,
            website=primary.website or fallback.website,
            supply=primary.supply or fallback.supply,
            created_at=primary.created_at or fallback.created_at)

    async def enrich_unknown(self):
        while True:
            lookup_launch = bool(self.pending)
            if lookup_launch:
                mint = self.pending.pop()
            elif self.profile_candidates:
                mint = self.profile_candidates.pop()
            else:
                await asyncio.sleep(.25)
                continue
            if lookup_launch and mint in self.tokens:
                self.pending_events.pop(mint, None)
                continue
            profile = await self.fetch_profile(mint)
            now = time.time()
            token = self.tokens.get(mint)
            if token is None and profile and 0 <= now - profile.created_at <= MAX_AGE:
                token = Token(
                    mint=mint, launched_at=profile.created_at,
                    phase='PumpSwap' if profile.graduated else 'Pump.fun')
                self.tokens[mint] = token
                for event in self.pending_events.pop(mint, ()):
                    self.process_trade(token, event, now)
                self.lookup_attempts.pop(mint, None)
            if token and profile:
                before = (token.twitter, token.telegram, token.website,
                          token.banner, token.image)
                token.name = profile.name or token.name
                token.symbol = profile.symbol or token.symbol
                token.image = profile.image or token.image
                token.banner = profile.banner or token.banner
                token.twitter = profile.twitter or token.twitter
                token.telegram = profile.telegram or token.telegram
                token.website = profile.website or token.website
                token.supply = token.supply or profile.supply
                if profile.market_cap_usd:
                    token.reported_mc_usd = profile.market_cap_usd
                    token.reported_mc_at = now
                self.changed.add(mint)
                if before != (token.twitter, token.telegram, token.website,
                              token.banner, token.image):
                    self.queue_alert_update(mint)
            if lookup_launch and mint not in self.tokens and mint in self.pending_events:
                attempts = self.lookup_attempts.get(mint, 0) + 1
                self.lookup_attempts[mint] = attempts
                if attempts <= 3:
                    self.pending.add(mint)
                else:
                    self.pending_events.pop(mint, None)
                    self.lookup_attempts.pop(mint, None)
            await asyncio.sleep(.5)

    def flush(self):
        if self.changed:
            self.storage.save_launches([self.tokens[mint] for mint in self.changed
                                        if mint in self.tokens])
            self.changed.clear()
        if self.reasons:
            self.storage.mark_reasons(self.reasons)
            self.reasons.clear()
        self.storage.save_checkpoint(self.last_slot, time.time())

    async def housekeeping(self):
        while True:
            await asyncio.sleep(10)
            now = time.time()
            self.flush()
            for mint, token in list(self.tokens.items()):
                if now - token.launched_at > MAX_AGE:
                    del self.tokens[mint]
            if self.social:
                self.social.prune(set(self.tokens))
            LOG.info('LIVE connected=%s events=%d tracked=%d signals=%d sol_usd=%s',
                     self.stream.connected, self.intake_count, len(self.tokens),
                     self.call_count, round(self.sol_usd, 2))
            if self.social:
                LOG.info('SOCIAL requests=%d failures=%d queued=%d reports=%d',
                         self.social.queries, self.social.failures,
                         len(self.social.pending), len(self.social.reports))
