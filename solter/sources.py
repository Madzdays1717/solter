"""Free public data: PumpPortal launch/migration events, DexScreener, token metadata."""
from __future__ import annotations

import json

from .chain import PUMP_SUPPLY, WSOL, valid_address

PUMP_WS = 'wss://pumpportal.fun/api/data'
DEX = 'https://api.dexscreener.com'


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and 0 <= number < 1e18 else None


def parse_portal(text) -> tuple[str, dict] | None:
    """('create', info) or ('migrate', {'mint'}) from one PumpPortal message."""
    try:
        event = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(event, dict) or not valid_address(event.get('mint')):
        return None
    mint = event['mint']
    kind = str(event.get('txType', '')).lower()
    if kind in ('migrate', 'migration') or (event.get('pool') and kind != 'create'):
        return 'migrate', {'mint': mint}
    if kind != 'create' and not ('name' in event and 'symbol' in event):
        return None
    creator = event.get('traderPublicKey')
    dev_tokens = _number(event.get('initialBuy'))
    if dev_tokens is not None and dev_tokens > PUMP_SUPPLY:
        dev_tokens /= 10**6                  # raw units instead of UI units
    uri = event.get('uri')
    return 'create', {
        'mint': mint,
        'symbol': str(event.get('symbol') or '')[:32],
        'name': str(event.get('name') or '')[:80],
        'uri': uri[:300] if isinstance(uri, str) and uri.startswith(('https://', 'ipfs://')) else None,
        'creator': creator if valid_address(creator) else None,
        'dev_sol': _number(event.get('solAmount')),
        'dev_tokens': dev_tokens,
    }


def sol_usd_from(pairs) -> float | None:
    """SOL price from DexScreener's WSOL pairs: the deepest SOL/USDC or SOL/USDT pair."""
    best, depth = None, 0.0
    for pair in pairs if isinstance(pairs, list) else []:
        if not isinstance(pair, dict) or pair.get('chainId') != 'solana':
            continue
        if (pair.get('baseToken') or {}).get('address') != WSOL:
            continue
        if str((pair.get('quoteToken') or {}).get('symbol', '')).upper() not in ('USDC', 'USDT'):
            continue
        price = _number(pair.get('priceUsd'))
        liquidity = _number((pair.get('liquidity') or {}).get('usd')) or 0
        if price and 5 < price < 100000 and liquidity > depth:
            best, depth = price, liquidity
    return best


def pumpswap_pair(pairs, mint: str) -> dict | None:
    """The deepest PumpSwap pair DexScreener lists for `mint`."""
    found = [p for p in (pairs if isinstance(pairs, list) else [])
             if isinstance(p, dict) and p.get('chainId') == 'solana'
             and (p.get('baseToken') or {}).get('address') == mint
             and str(p.get('dexId', '')).lower().replace('-', '') in ('pumpswap', 'pumpamm')]
    return max(found, key=lambda p: _number((p.get('liquidity') or {}).get('usd')) or 0, default=None)


def pair_facts(pair: dict | None) -> dict:
    if not pair:
        return {}
    return {'address': pair.get('pairAddress') if valid_address(pair.get('pairAddress')) else None,
            'price_sol': _number(pair.get('priceNative')),
            'liquidity_usd': _number((pair.get('liquidity') or {}).get('usd')),
            'symbol': str((pair.get('baseToken') or {}).get('symbol') or '')[:32],
            'name': str((pair.get('baseToken') or {}).get('name') or '')[:80],
            'image': (pair.get('info') or {}).get('imageUrl')}
