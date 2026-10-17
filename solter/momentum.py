"""Lightweight, free DexScreener radar: volume + transaction pressure + active MC.

No wallet tracking and no claim that DexScreener lists every just-created token.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


def number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def pick_pairs(payload, wanted: set[str]) -> dict[str, dict]:
    """Only actual Solana BASE token matches; never confuse a quote token's MC."""
    result = {}
    if not isinstance(payload, list):
        return result
    for pair in payload:
        if not isinstance(pair, dict) or pair.get('chainId') != 'solana':
            continue
        mint = (pair.get('baseToken') or {}).get('address')
        if mint not in wanted:
            continue
        # Prefer the most liquid pool, not an arbitrary tiny duplicate pool.
        if mint not in result or (number((pair.get('liquidity') or {}).get('usd')) or 0) > (number((result[mint].get('liquidity') or {}).get('usd')) or 0):
            result[mint] = pair
    return result


@dataclass(frozen=True)
class Signal:
    market_cap_usd: float
    volume_5m_usd: float
    buys_5m: int
    sells_5m: int
    change_5m_pct: float
    observed_mc_change_pct: float | None
    liquidity_usd: float | None
    mc_is_fdv: bool = False


def signal(pair: dict, previous: dict | None, *, min_mc: float, max_mc: float,
           min_volume: float, min_buys: int, min_buy_ratio: float,
           min_change_pct: float) -> Signal | None:
    """Radar only: rolling 5m activity and MC rising from observed samples or 5m price.

    No pair / missing data == no signal, never auto-classified as scam or safe.
    """
    real_mc = number(pair.get('marketCap'))
    mc = real_mc if real_mc is not None else number(pair.get('fdv'))
    vol = number((pair.get('volume') or {}).get('m5'))
    txn = (pair.get('txns') or {}).get('m5') or {}
    if not isinstance(txn, dict):
        return None
    buys, sells = number(txn.get('buys')), number(txn.get('sells'))
    try:
        price_change = float((pair.get('priceChange') or {}).get('m5'))
    except (TypeError, ValueError):
        price_change = float('nan')
    if (mc is None or vol is None or buys is None or sells is None
            or not math.isfinite(price_change)):
        return None
    prev_mc = number(previous.get('market_cap_usd')) if previous else None
    change = (mc / prev_mc - 1) * 100 if prev_mc and prev_mc > 0 else None
    if change is not None and change < -2.0:  # avoid a falling MC on stale rolling price change
        return None
    if not (min_mc <= mc <= max_mc and vol >= min_volume and buys >= min_buys
            and buys >= max(1, sells) * min_buy_ratio
            and (price_change >= min_change_pct or
                 (change is not None and change >= min_change_pct))):
        return None
    return Signal(mc, vol, int(buys), int(sells), price_change,
                  change, number((pair.get('liquidity') or {}).get('usd')),
                  real_mc is None)
