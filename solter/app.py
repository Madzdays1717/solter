"""Run market intake, momentum analysis, and alert delivery."""

import asyncio
from collections import deque
import logging
import time

import aiohttp

from .config import Config
from .events import Event, number, parse_event
from .momentum import MAX_AGE, Token
from .storage import Storage
from .stream import TradeStream
from .telegram import Telegram

LOG = logging.getLogger('solter')
COIN_API = 'https://frontend-api-v3.pump.fun/coins-v2/'


class App:
    def __init__(self, config: Config, storage: Storage):
        self.config = config
        self.storage = storage
        self.tokens = storage.load_launches()
        self.pending: set[str] = set()
        self.pending_events: dict[str, deque[Event]] = {}
        self.sol_usd = 0.0
        self.last_slot = storage.checkpoint()[0]
        self.last_flush = time.monotonic()
        self.seen: set[tuple[str, str, str, str]] = set()
        self.seen_order: deque[tuple[str, str, str, str]] = deque()
        self.changed: set[str] = set()
        self.notified: set[str] = set()
        self.image_candidates: set[str] = set()
        self.image_requested: set[str] = set()
        self.lookup_attempts: dict[str, int] = {}
        self._reasons: dict[str, str] = {}
        self.alert_tasks: set[asyncio.Task] = set()
        self.intake_count = 0
        self.call_count = 0
        self.stream = TradeStream(self.on_message, storage.checkpoint)
        self.session: aiohttp.ClientSession | None = None
        self.telegram: Telegram | None = None

    async def run(self):
        timeout = aiohttp.ClientTimeout(total=12, connect=6)
        async with aiohttp.ClientSession(timeout=timeout,
                headers={'User-Agent': 'Solter/3.2'}) as session:
            self.session = session
            self.telegram = Telegram(session, self.config.telegram_token,
                                     self.config.telegram_chat_id)
            jobs = [
                asyncio.create_task(self.stream.run(session), name='market-stream'),
                asyncio.create_task(self.housekeeping(), name='housekeeping'),
                asyncio.create_task(self.enrich_unknown(), name='launch-lookup'),
            ]
            LOG.info('Solter 3.2 running; cached launches=%d', len(self.tokens))
            try:
                await asyncio.gather(*jobs)
            finally:
                for job in jobs:
                    job.cancel()
                await asyncio.gather(*jobs, return_exceptions=True)
                self.flush()
                if self.alert_tasks:
                    await asyncio.wait(self.alert_tasks, timeout=8)

    def on_message(self, kind: str, data: object):
        if kind == 'sol_price':
            if isinstance(data, dict) and 1 < number(data.get('priceUSD')) < 2000:
                self.sol_usd = number(data['priceUSD'])
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
        if not token.image and len(token.observations) >= 3 and token.mint not in self.image_requested:
            self.image_requested.add(token.mint)
            self.image_candidates.add(token.mint)
        signal, reason = token.decide(now)
        if signal is None:
            # Keep the reason locally; persist it in the periodic flush.
            self.reasons[token.mint] = reason
            return
        if self.storage.already_alerted(token.mint):
            self.notified.add(token.mint)
            return
        if self.storage.reserve(token.mint):
            self.notified.add(token.mint)
            self.call_count += 1
            LOG.info('SIGNAL %s age=%ds buys=%d net=%.2f SOL',
                     token.mint, int(signal.age_seconds), signal.buys,
                     signal.buy_sol - signal.sell_sol)
            task = asyncio.create_task(self.deliver(signal))
            self.alert_tasks.add(task)
            task.add_done_callback(self.alert_tasks.discard)

    @property
    def reasons(self):
        return self._reasons

    async def deliver(self, signal):
        try:
            state, message = await self.telegram.send(signal, self.sol_usd)
        except Exception:
            LOG.exception('Alert delivery exception')
            state, message = 'uncertain', None
        self.storage.finish(signal.mint, state, message)
        LOG.info('ALERT %s %s', state, signal.mint)

    async def enrich_unknown(self):
        while True:
            if self.pending:
                mint = self.pending.pop()
                lookup_launch = True
            elif self.image_candidates:
                mint = self.image_candidates.pop()
                lookup_launch = False
            else:
                await asyncio.sleep(.5)
                continue
            if lookup_launch and mint in self.tokens:
                self.pending_events.pop(mint, None)
                continue
            try:
                async with self.session.get(COIN_API + mint, timeout=aiohttp.ClientTimeout(total=6)) as response:
                    if response.status == 200:
                        info = await response.json(content_type=None)
                        if isinstance(info, dict):
                            now = time.time()
                            if not lookup_launch:
                                token = self.tokens.get(mint)
                                if token and info.get('image_uri'):
                                    token.image = str(info['image_uri'])[:1024]
                                    self.changed.add(mint)
                            else:
                                launched = number(info.get('created_timestamp'))
                                if launched > 1e11:
                                    launched /= 1000
                                if 0 <= now - launched <= MAX_AGE and launched > 0:
                                    token = Token(mint=mint, launched_at=launched,
                                        name=str(info.get('name') or '')[:80],
                                        symbol=str(info.get('symbol') or '')[:28],
                                        phase='PumpSwap' if info.get('complete') else 'Pump.fun',
                                        image=str(info.get('image_uri') or '')[:1024])
                                    self.tokens[mint] = token
                                    self.changed.add(mint)
                                    for event in self.pending_events.pop(mint, ()):
                                        self.process_trade(token, event, now)
                                    self.lookup_attempts.pop(mint, None)
                    elif response.status in (401, 403, 429):
                        LOG.warning('Pump.fun metadata access HTTP %d', response.status)
                        await asyncio.sleep(3)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            if lookup_launch and mint not in self.tokens and mint in self.pending_events:
                count = self.lookup_attempts.get(mint, 0) + 1
                self.lookup_attempts[mint] = count
                if count <= 3:
                    self.pending.add(mint)
                else:
                    self.pending_events.pop(mint, None)
                    self.lookup_attempts.pop(mint, None)
            await asyncio.sleep(1.2)

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
            LOG.info('LIVE connected=%s events=%d tracked=%d signals=%d sol_usd=%s',
                     self.stream.connected, self.intake_count, len(self.tokens),
                     self.call_count, round(self.sol_usd, 2))
