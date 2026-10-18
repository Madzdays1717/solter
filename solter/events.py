"""Validated events from the transaction stream."""

from dataclasses import dataclass
import math
import time

from .addresses import SOL, valid_address


@dataclass(frozen=True)
class Fill:
    wallet: str
    side: str
    sol: float


@dataclass(frozen=True)
class Event:
    mint: str
    action: str
    protocol: str
    timestamp: float
    slot: int
    signature: str
    pool: str
    price_sol: float = 0.0
    volume_sol: float = 0.0
    market_cap_sol: float = 0.0
    supply: float = 0.0
    pool_sol: float = 0.0
    fills: tuple[Fill, ...] = ()
    name: str = ''
    symbol: str = ''
    creator: str = ''
    initial_buy_sol: float = 0.0
    restricted: bool = False
    authority_observed: bool = False


def number(value: object) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) and parsed >= 0 else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def parse_event(raw: object, *, now: float | None = None) -> Event | None:
    if not isinstance(raw, dict):
        return None
    action = raw.get('action')
    protocol = raw.get('protocol')
    if protocol not in ('PUMPFUN', 'PUMPFUN_MAYHEM', 'PUMPSWAP'):
        return None
    if action not in ('create', 'buy', 'sell', 'migrate', 'createPool'):
        return None
    mint = raw.get('mint')
    if not valid_address(mint):
        return None
    if action in ('buy', 'sell') and raw.get('quoteMint') != SOL:
        return None

    now = time.time() if now is None else now
    stamp = number(raw.get('timestamp'))
    if stamp < 1_600_000_000 or stamp > now + 30:
        return None

    price = number(raw.get('price')) if action in ('buy', 'sell') else 0.0
    volume = number(raw.get('quoteAmount')) if action in ('buy', 'sell') else 0.0
    if action in ('buy', 'sell') and (not price or not volume):
        return None

    fills: list[Fill] = []
    if action in ('buy', 'sell'):
        breakdown = raw.get('breakdown')
        if isinstance(breakdown, list):
            for part in breakdown[:64]:
                if not isinstance(part, dict):
                    continue
                wallet = part.get('trader')
                side = part.get('action')
                amount = number(part.get('quoteAmount'))
                if valid_address(wallet) and side in ('buy', 'sell') and amount > 0:
                    fills.append(Fill(wallet, side, amount))
        if not fills:
            traders = raw.get('tradersInvolved')
            # A stream event may aggregate many wallets. Without breakdown, do
            # not assign the complete volume to each participant.
            if isinstance(traders, list) and len(traders) == 1 and valid_address(traders[0]):
                fills.append(Fill(traders[0], action, volume))

    extensions = raw.get('tokenExtensions')
    risky = {'transferFeeConfig', 'permanentDelegate'}
    authority_observed = action == 'create' and (
        'mintAuthority' in raw or 'freezeAuthority' in raw or 'tokenExtensions' in raw
    )
    restricted = bool(raw.get('mintAuthority') or raw.get('freezeAuthority'))
    if isinstance(extensions, list):
        restricted |= bool(risky.intersection(extensions))

    creator = raw.get('creator')
    initial_buy = raw.get('initialBuy')
    return Event(
        mint=mint, action=action, protocol=protocol, timestamp=stamp,
        slot=int(number(raw.get('block'))),
        signature=str(raw.get('signature') or '')[:120],
        pool=str(raw.get('pool') or raw.get('toPool') or '')[:80],
        price_sol=price, volume_sol=volume,
        market_cap_sol=number(raw.get('marketCapQuote')),
        supply=number(raw.get('supply')) if action == 'create' else 0.0,
        pool_sol=number(raw.get('quoteInPool')),
        fills=tuple(fills),
        name=str(raw.get('name') or '')[:80],
        symbol=str(raw.get('symbol') or '')[:28],
        creator=creator if valid_address(creator) else '',
        initial_buy_sol=number(initial_buy.get('quoteAmount')) if isinstance(initial_buy, dict) else 0.0,
        restricted=restricted, authority_observed=authority_observed,
    )
