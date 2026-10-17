"""One pure, explainable momentum decision for every candidate.

The provider reports transactions, not unique buyers. Signals are not token
security assessments, and strong market activity can still be manipulated.
"""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MarketSnapshot:
    mint: str
    symbol: str
    name: str
    dex: str
    pair: str
    age_seconds: float
    market_cap: float
    liquidity: float
    volume_5m: float
    buys_5m: int
    sells_5m: int
    change_5m: float
    price_usd: float | None
    image: str | None
    source_observed_at: float


@dataclass(frozen=True)
class Decision:
    accepted: bool
    reason: str
    strength: int = 0
    high_risk_momentum: bool = False


def evaluate(snapshot: MarketSnapshot) -> Decision:
    """Reject weak data/momentum; never reject solely for high MC or token age."""
    numbers = (snapshot.market_cap, snapshot.liquidity, snapshot.volume_5m,
               snapshot.change_5m, snapshot.age_seconds)
    if not all(math.isfinite(n) for n in numbers):
        return Decision(False, 'data pasar tidak lengkap')
    if snapshot.market_cap < 3000:
        return Decision(False, 'kapitalisasi pasar terlalu kecil')
    if snapshot.liquidity < 5000:
        return Decision(False, 'likuiditas rendah')
    if snapshot.volume_5m < 1500:
        return Decision(False, 'volume 5 menit rendah')
    if snapshot.buys_5m < 12 or snapshot.buys_5m < 1.5 * max(1, snapshot.sells_5m):
        return Decision(False, 'tekanan beli kurang kuat')
    if snapshot.change_5m < 2:
        return Decision(False, 'harga belum menunjukkan momentum naik')

    breakout = snapshot.market_cap > 300_000
    if breakout:
        if snapshot.volume_5m < .02 * snapshot.market_cap:
            return Decision(False, 'volume belum kuat untuk breakout')
        if snapshot.liquidity < .01 * snapshot.market_cap:
            return Decision(False, 'likuiditas belum kuat untuk breakout')
        if snapshot.buys_5m < 24 or snapshot.buys_5m < 1.7 * max(1, snapshot.sells_5m):
            return Decision(False, 'tekanan beli breakout kurang kuat')

    strength = min(100, round(
        25 + min(25, max(0, snapshot.buys_5m - snapshot.sells_5m))
        + min(25, snapshot.volume_5m / max(1, snapshot.liquidity) * 35)
        + min(25, max(0, snapshot.change_5m) * .6)
    ))
    return Decision(True, 'breakout' if breakout else 'momentum awal',
                    strength, snapshot.change_5m > 85)
