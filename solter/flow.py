"""Live buy flow measured in SOL: the evidence a call needs.

Counting transactions is easy to fake (volume bots, dust buys, one wallet
split into many). Every gate here is measured in SOL from decoded trade
events, after dust is removed, and the creator's own trades never count
as demand. None of this predicts returns; it filters for real, current,
spread-out buying.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from .events import Trade


@dataclass(frozen=True)
class Rules:
    window: int = 180             # seconds of flow judged
    min_buys: int = 8             # non-dust buys
    min_buyers: int = 6           # distinct non-dust buyers (not the creator)
    min_net_sol: float = 2.0      # buy SOL minus sell SOL in the window
    dust_sol: float = 0.05        # buys smaller than this are ignored
    max_wallet_share: float = .30  # largest buyer's share of buy SOL
    max_bundle_share: float = .50  # buy SOL in slots where 3+ wallets bought together
    max_sell_ratio: float = .60   # sell SOL / buy SOL
    min_volume_sol: float = 0.0   # all SOL traded in the window (buys of any size and sells)
    max_churn: float = 1.0        # share of that volume from wallets that both bought and sold
    max_lag: float = 30           # newest buy must be this fresh (seconds)
    max_run: float = 1.0          # price rise across the window (1.0 = +100%)
    min_span: float = 20          # seconds between first and last buy
    min_recent_buyers: int = 3
    min_burst: int = 3
    recent: float = 45            # seconds that count as "now"


@dataclass(frozen=True)
class Flow:
    ok: bool
    reason: str
    buys: int = 0
    sells: int = 0
    buyers: int = 0
    recent_buyers: int = 0
    burst: int = 0
    buy_sol: float = 0.0
    sell_sol: float = 0.0
    top_share: float = 0.0
    bundle_share: float = 0.0
    run: float | None = None
    creator_sold: bool = False
    volume_sol: float = 0.0
    churn: float = 0.0

    @property
    def net_sol(self) -> float:
        return self.buy_sol - self.sell_sol

    @property
    def sell_ratio(self) -> float:
        return self.sell_sol / self.buy_sol if self.buy_sol else 0.0


def evaluate(trades: list[Trade], now: float, rules: Rules = Rules(),
             creator: str | None = None) -> Flow:
    """Judge the most recent `rules.window` seconds of `trades` (all trades seen while watching)."""
    creator_sold = bool(creator) and any(t.side == 'sell' and t.wallet == creator for t in trades)
    window = sorted((t for t in trades if -5 <= now - t.at <= rules.window),
                    key=lambda t: (t.at, t.slot or 0, t.idx))
    unknown = sum(1 for t in window if t.sol is None)
    buys = [t for t in window if t.side == 'buy' and (t.sol or 0) >= rules.dust_sol
            and t.wallet != creator]
    sells = [t for t in window if t.side == 'sell']
    buy_sol = sum(t.sol for t in buys)
    sell_sol = sum(t.sol or 0 for t in sells)
    buyers = {t.wallet for t in buys}
    recent = {t.wallet for t in buys if now - t.at <= rules.recent}
    burst = max((sum(1 for u in buys if 0 <= u.at - t.at <= 20) for t in buys), default=0)
    per_wallet = Counter()
    for t in buys:
        per_wallet[t.wallet] += t.sol
    top_share = max(per_wallet.values()) / buy_sol if buy_sol else 0.0
    by_slot = defaultdict(list)
    for t in buys:
        if t.slot is not None:
            by_slot[t.slot].append(t)
    bundled = sum(sum(t.sol for t in group) for group in by_slot.values()
                  if len({t.wallet for t in group}) >= 3)
    bundle_share = bundled / buy_sol if buy_sol else 0.0
    # Volume counts every trade; churn is the part of it from wallets that bought and also
    # sold inside the window (volume bots and flippers), which inflates volume without demand.
    volume = sum(t.sol or 0 for t in window)
    by_wallet: dict = defaultdict(lambda: [0.0, False, False])
    for t in window:
        entry = by_wallet[t.wallet]
        entry[0] += t.sol or 0
        entry[1 if t.side == 'buy' else 2] = True
    churned = sum(v for v, bought, sold in by_wallet.values() if bought and sold)
    churn = churned / volume if volume else 0.0
    prices = [t.price for t in window if t.price]
    run = prices[-1] / prices[0] - 1 if len(prices) >= 2 else None

    flow = dict(buys=len(buys), sells=len(sells), buyers=len(buyers), recent_buyers=len(recent),
                burst=burst, buy_sol=buy_sol, sell_sol=sell_sol, top_share=top_share,
                bundle_share=bundle_share, run=run, creator_sold=creator_sold,
                volume_sol=volume, churn=churn)

    def verdict(reason: str, ok: bool = False) -> Flow:
        return Flow(ok, reason, **flow)

    if creator_sold:
        return verdict('creator sold')
    if window and unknown > len(window) * .2:
        return verdict('trade sizes unknown')
    if len(buys) < rules.min_buys:
        return verdict('few buys')
    if len(buyers) < rules.min_buyers:
        return verdict('few buyers')
    if buy_sol - sell_sol < rules.min_net_sol:
        return verdict('weak net inflow')
    if volume < rules.min_volume_sol:
        return verdict('low volume')
    if len(recent) < rules.min_recent_buyers or burst < rules.min_burst:
        return verdict('momentum faded')
    if now - buys[-1].at > rules.max_lag:
        return verdict('stale flow')
    if sell_sol > buy_sol * rules.max_sell_ratio:
        return verdict('sell pressure')
    if churn > rules.max_churn:
        return verdict('wash trading')
    if top_share > rules.max_wallet_share:
        return verdict('one wallet dominates')
    if bundle_share > rules.max_bundle_share:
        return verdict('bundled buys')
    if buys[-1].at - buys[0].at < rules.min_span:
        return verdict('too short')
    if run is not None and run > rules.max_run:
        return verdict('overextended')
    if run is not None and run < -.30:
        return verdict('falling')
    return verdict('flow confirmed', True)
