"""Rolling market observations and momentum decisions."""

from collections import deque
from dataclasses import dataclass, field, replace

from .events import Event
from .risk import RiskAnalyzer, RiskReport

WINDOW = 120
MAX_AGE = 3600


@dataclass(frozen=True)
class Signal:
    mint: str
    name: str
    symbol: str
    phase: str
    age_seconds: float
    buys: int
    sells: int
    buy_sol: float
    sell_sol: float
    buyers: int
    change_pct: float
    market_cap_sol: float
    pool_sol: float
    price_sol: float
    score: int
    timestamp: float
    risk: RiskReport
    image: str = ''
    warning: str = ''


@dataclass
class Token:
    mint: str
    launched_at: float
    name: str = ''
    symbol: str = ''
    phase: str = 'Pump.fun'
    pool: str = ''
    restricted: bool = False
    image: str = ''
    creator: str = ''
    initial_buy_sol: float = 0.0
    supply: float = 0.0
    authority_observed: bool = False
    early_buyers: set[str] = field(default_factory=set)
    creator_buy_sol: float = 0.0
    creator_sell_sol: float = 0.0
    observations: deque[Event] = field(default_factory=lambda: deque(maxlen=900))
    last_event_at: float = 0.0

    def add(self, event: Event, now: float) -> None:
        self.last_event_at = max(self.last_event_at, event.timestamp)
        if event.action == 'create':
            self.creator = event.creator or self.creator
            self.initial_buy_sol = max(self.initial_buy_sol, event.initial_buy_sol)
            self.supply = event.supply or self.supply
            self.authority_observed |= event.authority_observed
            self.restricted |= event.restricted
        if event.action == 'migrate' or event.protocol == 'PUMPSWAP':
            if self.phase != 'PumpSwap':
                self.observations.clear()  # A new pool has a separate price history.
            self.phase = 'PumpSwap'
        if event.pool:
            self.pool = event.pool
        if event.action in ('buy', 'sell') and event.timestamp >= now - WINDOW:
            if event.market_cap_sol <= 0 and self.supply > 0 and event.price_sol > 0:
                event = replace(event, market_cap_sol=event.price_sol * self.supply)
            self.observations.append(event)
            for fill in event.fills:
                if fill.side == 'buy' and event.timestamp <= self.launched_at + 30:
                    self.early_buyers.add(fill.wallet)
                if self.creator and fill.wallet == self.creator:
                    if fill.side == 'buy':
                        self.creator_buy_sol += fill.sol
                    elif fill.side == 'sell':
                        self.creator_sell_sol += fill.sol
        self._expire(now)

    def _expire(self, now: float) -> None:
        # The stream generally arrives ordered; late replayed events are removed
        # during this bounded cleanup without a full-history scan.
        while self.observations and self.observations[0].timestamp < now - WINDOW:
            self.observations.popleft()

    def decide(self, now: float) -> tuple[Signal | None, str]:
        age = now - self.launched_at
        if age < 0 or age > MAX_AGE:
            return None, 'outside launch window'
        if self.restricted:
            return None, 'restricted mint controls'
        if now - self.last_event_at > 20:
            return None, 'stale activity'
        events = list(self.observations)
        buys = [event for event in events if event.action == 'buy']
        sells = [event for event in events if event.action == 'sell']
        if not buys:
            return None, 'awaiting trades'
        buy_sol = sum(e.volume_sol for e in buys)
        sell_sol = sum(e.volume_sol for e in sells)
        buyers = {fill.wallet for e in buys for fill in e.fills if fill.side == 'buy'}
        if len(buys) < (10 if age > 1800 else 7):
            return None, 'buy activity building'
        if len(buyers) < 4:
            return None, 'low observed buyer diversity'
        if buy_sol < (2 if age > 1800 else 1):
            return None, 'buy volume building'
        if buy_sol < 1.5 * max(.05, sell_sol) or buy_sol - sell_sol < (.8 if age > 1800 else .5):
            return None, 'sell pressure'
        priced = [event for event in events if event.price_sol > 0]
        if len(priced) < 2:
            return None, 'awaiting price history'
        first = min(priced, key=lambda e: (e.timestamp, e.slot))
        last = max(priced, key=lambda e: (e.timestamp, e.slot))
        change = (last.price_sol / first.price_sol - 1) * 100
        if change < (3 if age > 1800 else 2):
            return None, 'price momentum building'
        latest = max((e for e in events if e.price_sol > 0 and e.pool_sol > 0),
                     key=lambda e: (e.timestamp, e.slot), default=None)
        if latest is None or latest.timestamp < now - 20:
            return None, 'market data incomplete'
        market_cap_sol = latest.market_cap_sol
        if market_cap_sol <= 0 and self.supply > 0:
            market_cap_sol = latest.price_sol * self.supply
        if self.phase == 'PumpSwap' and latest.pool_sol < 8:
            return None, 'shallow pool'
        if self.phase == 'Pump.fun' and age < 8:
            return None, 'launch settling'

        risk = RiskAnalyzer.analyze(
            events, creator_bought_sol=self.initial_buy_sol + self.creator_buy_sol,
            creator_sold_sol=self.creator_sell_sol,
            early_buyers=len(self.early_buyers),
            authority_observed=self.authority_observed,
        )
        if (risk.coverage == 'observed-trade-only'
                and risk.buyer_concentration_pct is not None
                and risk.buyer_concentration_pct >= 92 and len(buyers) >= 4):
            return None, 'extreme buying concentration'
        recent_creator_sells = sum(
            fill.sol for event in sells for fill in event.fills
            if self.creator and fill.wallet == self.creator and fill.side == 'sell'
        )
        if recent_creator_sells >= buy_sol and recent_creator_sells >= 1:
            return None, 'substantial recent developer selling'
        score = round(min(100, 30 + min(25, (buy_sol - sell_sol) * 6)
                               + min(20, len(buyers) * 2)
                               + min(25, change * .7)))
        return Signal(
            mint=self.mint, name=self.name or 'Token', symbol=self.symbol or 'TOKEN',
            phase=self.phase, age_seconds=age, buys=len(buys), sells=len(sells),
            buy_sol=buy_sol, sell_sol=sell_sol, buyers=len(buyers), change_pct=change,
            market_cap_sol=market_cap_sol, pool_sol=latest.pool_sol,
            price_sol=latest.price_sol, score=score, timestamp=latest.timestamp,
            risk=risk, image=self.image,
            warning='High volatility' if change > 85 else '',
        ), 'momentum confirmed'
