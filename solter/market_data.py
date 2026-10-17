"""Decode free Solana market feeds into consistent market snapshots."""

from __future__ import annotations

import json
import math
import time

from .addresses import QUOTE_MINTS, valid_mint
from .signals import MarketSnapshot

DEXES = frozenset(('pumpswap', 'pumpamm', 'raydium', 'raydiumclmm',
                   'meteora', 'meteoradlmm', 'orca'))


def finite_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def pump_event(message: str) -> tuple[str, str] | None:
    """Extract mint from launch/migration events, not paid trade streams."""
    try:
        event = json.loads(message)
    except (ValueError, TypeError):
        return None
    if not isinstance(event, dict):
        return None
    mint = event.get('mint')
    if not valid_mint(mint):
        return None
    kind = str(event.get('txType') or '').lower()
    if kind in ('migrate', 'migration') or (event.get('pool') and kind != 'create'):
        return 'migration', mint
    if kind == 'create' or ('name' in event and 'symbol' in event):
        return 'launch', mint
    return None


def gecko_tokens(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    found = []
    for item in payload.get('data') or []:
        if not isinstance(item, dict):
            continue
        base = (((item.get('relationships') or {}).get('base_token') or {}).get('data') or {}).get('id')
        if isinstance(base, str) and base.startswith('solana_'):
            mint = base[len('solana_'):]
            if valid_mint(mint):
                found.append(mint)
    return list(dict.fromkeys(found))


def dex_profiles(payload: object) -> dict[str, str | None]:
    """Address -> optional image URL. A paid boost is discovery, never quality."""
    found = {}
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict) or item.get('chainId') != 'solana':
            continue
        mint = item.get('tokenAddress')
        if valid_mint(mint):
            found[mint] = item.get('header') or item.get('icon')
    return found


def normalize_pair(pair: dict, mint: str, observed_at: float) -> MarketSnapshot | None:
    if not isinstance(pair, dict) or pair.get('chainId') != 'solana':
        return None
    base = pair.get('baseToken')
    quote = pair.get('quoteToken')
    if not isinstance(base, dict) or not isinstance(quote, dict):
        return None
    if base.get('address') != mint or quote.get('address') not in QUOTE_MINTS:
        return None
    dex = str(pair.get('dexId') or '').lower().replace('-', '').replace('_', '')
    if dex not in DEXES or not valid_mint(pair.get('pairAddress')):
        return None
    transactions = pair.get('txns')
    volumes = pair.get('volume')
    liquidity_info = pair.get('liquidity')
    changes = pair.get('priceChange')
    if not all(isinstance(item, dict) for item in (transactions, volumes, liquidity_info, changes)):
        return None
    tx = transactions.get('m5')
    if not isinstance(tx, dict):
        return None
    volume = finite_number(volumes.get('m5'))
    liquidity = finite_number(liquidity_info.get('usd'))
    cap = finite_number(pair.get('marketCap'))
    # Some pools provide FDV but not market cap: FDV is not an equivalent metric.
    rise = finite_number(changes.get('m5'))
    buys, sells = finite_number(tx.get('buys')), finite_number(tx.get('sells'))
    values = (volume, liquidity, cap, rise, buys, sells)
    if any(v is None for v in values):
        return None
    if any(v < 0 for v in (volume, liquidity, cap, buys, sells)) or rise < -100:
        return None
    created_ms = finite_number(pair.get('pairCreatedAt'))
    age = max(0, observed_at - created_ms / 1000) if created_ms else 0
    info = pair.get('info') or {}
    info = info if isinstance(info, dict) else {}
    return MarketSnapshot(
        mint=mint, symbol=str(base.get('symbol') or '?')[:32],
        name=str(base.get('name') or 'Token')[:80], dex=dex,
        pair=pair['pairAddress'], age_seconds=age,
        market_cap=cap, liquidity=liquidity, volume_5m=volume,
        buys_5m=int(buys), sells_5m=int(sells), change_5m=rise,
        price_usd=finite_number(pair.get('priceUsd')),
        image=info.get('header') or info.get('imageUrl'),
        source_observed_at=observed_at,
    )


def best_snapshot(pairs: object, mint: str, observed_at: float | None = None) -> MarketSnapshot | None:
    observed_at = time.time() if observed_at is None else observed_at
    best = None
    for pair in pairs if isinstance(pairs, list) else []:
        snap = normalize_pair(pair, mint, observed_at)
        if snap and (best is None or snap.liquidity > best.liquidity):
            best = snap
    return best
