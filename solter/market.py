"""Passive DEX discovery and pre-filtering. Never infer unique wallets from txn counts.

GeckoTerminal lists are *discovery*, not endorsements. DexScreener snapshots are
used only to allocate scarce live RPC watches, never as proof of genuine buyers.
"""
from __future__ import annotations

from .chain import valid_address
from .sources import _number, pumpswap_pair

GECKO = 'https://api.geckoterminal.com/api/v2'


def gecko_mints(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    found, seen = [], set()
    for item in payload.get('data') or []:
        if not isinstance(item, dict):
            continue
        token = (((item.get('relationships') or {}).get('base_token') or {}).get('data') or {}).get('id')
        if not isinstance(token, str) or not token.startswith('solana_'):
            continue
        mint = token[len('solana_'):]
        if valid_address(mint) and mint not in seen:
            seen.add(mint)
            found.append(mint)
    return found


def by_mint(pairs: object, allowed: set[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in pairs if isinstance(pairs, list) else []:
        if not isinstance(item, dict):
            continue
        mint = (item.get('baseToken') or {}).get('address')
        if mint not in allowed:
            continue
        pump = pumpswap_pair([item], mint)
        if pump and valid_address(pump.get('pairAddress')):
            prev = result.get(mint)
            if prev is None or (_number((pump.get('liquidity') or {}).get('usd')) or 0) > (
                    _number((prev.get('liquidity') or {}).get('usd')) or 0):
                result[mint] = pump
    return result


def market_snapshot(pair: dict, now: float) -> dict | None:
    """Last 5-minute activity and liquidity. Missing metrics must never be treated as 0."""
    tx = (pair.get('txns') or {}).get('m5') or {}
    buys = _number(tx.get('buys'))
    sells = _number(tx.get('sells'))
    volume = _number((pair.get('volume') or {}).get('m5'))
    liquidity = _number((pair.get('liquidity') or {}).get('usd'))
    change = pair.get('priceChange') or {}
    try:
        change5 = float(change['m5'])
    except (ValueError, TypeError, KeyError):
        change5 = None
    if None in (buys, sells, volume, liquidity):
        return None
    if change5 is not None and not -100 <= change5 <= 1_000_000:
        return None
    created_ms = _number(pair.get('pairCreatedAt'))
    created = created_ms / 1000 if created_ms and created_ms / 1000 <= now else now
    return dict(market_at=now, market_buys=int(buys), market_sells=int(sells),
                market_volume_usd=volume, market_liquidity_usd=liquidity,
                market_change_pct=change5, market_mcap_usd=_number(pair.get('marketCap')),
                pair_created_at=created,
                symbol=str((pair.get('baseToken') or {}).get('symbol') or '')[:32],
                name=str((pair.get('baseToken') or {}).get('name') or '')[:80])


def market_strength(t: dict) -> float:
    """Only a scheduling priority, not an investment score."""
    buys = t.get('market_buys') or 0
    sells = t.get('market_sells') or 0
    volume = t.get('market_volume_usd') or 0
    rise = t.get('market_change_pct') or 0
    return max(0.0, min(100.0, (buys - sells) * 2 + volume / 1500 + rise / 3))


def market_ready(t: dict, now: float, minimum_buys: int, minimum_volume: float,
                 liquidity: float) -> bool:
    return bool(t.get('market_at') and now - t['market_at'] <= 150
                and (t.get('market_buys') or 0) >= minimum_buys
                and (t.get('market_buys') or 0) >= 1.2 * max(1, t.get('market_sells') or 0)
                and (t.get('market_volume_usd') or 0) >= minimum_volume
                and (t.get('market_liquidity_usd') or 0) >= liquidity
                and (t.get('market_change_pct') is None or t['market_change_pct'] > -5))
