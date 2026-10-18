"""Risk indicators derived from observed trade activity."""

from collections import defaultdict
from dataclasses import dataclass

from .events import Event


@dataclass(frozen=True)
class RiskReport:
    buyer_concentration_pct: float | None
    creator_bought_sol: float
    creator_sold_sol: float
    early_buyers: int
    shared_transaction_wallets: int
    wallets_observed: int
    coverage: str
    warnings: tuple[str, ...]


class RiskAnalyzer:
    """Summarize known activity without implying token ownership."""

    @staticmethod
    def analyze(events: list[Event], *, creator_bought_sol: float,
                creator_sold_sol: float, early_buyers: int,
                authority_observed: bool) -> RiskReport:
        purchases: dict[str, float] = defaultdict(float)
        multi_wallet = 0
        incomplete = 0

        for event in events:
            if event.action not in ('buy', 'sell'):
                continue
            if not event.fills:
                incomplete += 1
                continue
            wallets = {fill.wallet for fill in event.fills}
            if len(wallets) > 1:
                multi_wallet += 1
            for fill in event.fills:
                if fill.side == 'buy':
                    purchases[fill.wallet] += fill.sol

        total = sum(purchases.values())
        share = (100 * sum(sorted(purchases.values(), reverse=True)[:3]) / total
                 if total else None)
        warnings = []
        if creator_sold_sol >= .3:
            warnings.append('Developer selling observed')
        if share is not None and share >= 80 and len(events) >= 8:
            warnings.append('Buying concentrated in few wallets')
        if multi_wallet:
            warnings.append('Multi-wallet transactions observed')
        if not authority_observed:
            warnings.append('Mint authority state unknown')
        return RiskReport(
            buyer_concentration_pct=share,
            creator_bought_sol=creator_bought_sol,
            creator_sold_sol=creator_sold_sol,
            early_buyers=early_buyers,
            shared_transaction_wallets=multi_wallet,
            wallets_observed=len(purchases),
            coverage='partial' if incomplete else 'observed-trade-only',
            warnings=tuple(warnings),
        )
