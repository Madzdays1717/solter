"""Fast, clearly unverified market-momentum signals from free DEX snapshots.

These are *transaction counts*, not unique wallets. No assertion about mint/freeze,
holders, wash trading, or creator is possible from these snapshots alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class MarketDecision:
    ok: bool
    reason: str
    strength: int = 0


def evaluate_market(data: dict, now: float, *, minimum_buys: int = 12,
                    minimum_volume: float = 1500, minimum_liquidity: float = 5000,
                    min_mcap: float = 3000, max_mcap: float = 300000,
                    minimum_change: float = 2, maximum_change: float = 85,
                    buy_sell_ratio: float = 1.5) -> MarketDecision:
    """Strict market-only trigger; never represents an on-chain safety check."""
    at = data.get('market_at')
    if not isinstance(at, (int, float)) or not 0 <= now - at <= 90:
        return MarketDecision(False, 'market snapshot stale')
    keys = ('market_buys', 'market_sells', 'market_volume_usd',
            'market_liquidity_usd', 'market_change_pct', 'market_mcap_usd')
    if any(not isinstance(data.get(k), (int, float)) or not isfinite(data[k]) for k in keys):
        return MarketDecision(False, 'market data incomplete')
    buys, sells = data['market_buys'], data['market_sells']
    volume, liquidity = data['market_volume_usd'], data['market_liquidity_usd']
    change, mcap = data['market_change_pct'], data['market_mcap_usd']
    if mcap < min_mcap:
        return MarketDecision(False, 'market mcap below minimum')
    # The old max_mcap was a hard ceiling. In market-only mode it now defines
    # the *breakout tier*: bigger pools require proportionally stronger evidence.
    # This avoids excluding strong large-cap momentum solely because discovery
    # arrived after a price jump. These are still unverified market alerts.
    breakout = bool(max_mcap and mcap > max_mcap)
    if breakout:
        minimum_liquidity = max(minimum_liquidity, mcap * .01)
        minimum_volume = max(minimum_volume, mcap * .02)
        minimum_buys = max(minimum_buys, 24)
        buy_sell_ratio = max(buy_sell_ratio, 1.7)
    if liquidity < minimum_liquidity:
        return MarketDecision(False, 'market thin liquidity')
    if buys < minimum_buys or buys < buy_sell_ratio * max(1, sells):
        return MarketDecision(False, 'market weak buy pressure')
    if volume < minimum_volume:
        return MarketDecision(False, 'market low volume')
    if change < minimum_change:
        return MarketDecision(False, 'market not rising')
    if change > maximum_change:
        # Very large early spikes remain excluded rather than FOMO-called.
        # An established breakout may still be included with exceptionally
        # strong pressure, but we do not chase limitless vertical candles.
        if not (breakout and change <= min(200, maximum_change * 2)
                and buys >= 50 and buys >= 2 * max(1, sells)
                and volume >= max(minimum_volume, mcap * .04)):
            return MarketDecision(False, 'market overextended')
    # Informational momentum strength, NOT a safety/quality/confidence score.
    strength = round(min(100, 25 + min(25, buys - sells) * 1.0
                         + min(25, volume / max(liquidity, 1) * 35)
                         + min(25, change * .6)))
    return MarketDecision(True, 'market breakout' if breakout else 'market early momentum', strength)
