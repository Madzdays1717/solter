"""Which launches deserve a live-watch slot.

Every launch PumpPortal announces enters the reservoir for free. A sweep
reads up to 100 bonding curves per `getMultipleAccounts` request; the real
SOL in a curve is the net money buyers put in, so its growth between two
sweeps is a cheap, manipulation-resistant traction measure (only real buys
add SOL). Live slots go to the fastest inflow instead of the first launch
that arrived, and a token that wakes up later gets a second chance.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .chain import LAMPORTS, PUMP_SUPPLY, Curve


@dataclass(frozen=True)
class ReservoirRules:
    first_check: float = 10          # seconds after discovery before the first look
    recheck: float = 30              # a quiet token is looked at once more after this
    alive_min_sol: float = 0.5       # community SOL: real SOL minus the creator's launch buy
    promote_min_rate: float = 0.5    # SOL per minute flowing in
    promote_min_sol: float = 1.0     # community SOL
    max_stalls: int = 5              # sweeps in a row without growth before an alive token is dropped
    dump_ratio: float = .4           # real SOL below this share of its peak = dumped
    max_promotions: int = 3
    max_progress: float = 85.0       # percent
    max_dev_buy_pct: float = 15.0
    max_creator_launches: int = 3    # 0 = off
    migrated_minutes: float = 15     # a migration stays a candidate this long
    stale_after: float = 300         # seconds; an older inflow reading cannot promote a token


def dev_buy_pct(token: dict) -> float | None:
    tokens = token.get('dev_tokens')
    if not isinstance(tokens, (int, float)) or tokens < 0:
        return None
    if tokens > PUMP_SUPPLY:          # raw units instead of UI units
        tokens /= 10**6
    return 100 * tokens / PUMP_SUPPLY


def classify(token: dict, curve: Curve | None, now: float, rules: ReservoirRules) -> dict:
    """Column updates for one swept token."""
    checks = (token.get('checks') or 0) + 1
    up: dict = {'checks': checks, 'checked_at': now}
    if curve is None:
        if checks >= 3:
            up.update(state='dead', reason='curve unreadable')
        return up
    if not curve.sol_quoted:
        up.update(state='dead', reason='non-SOL quote')
        return up
    if curve.mayhem:
        up.update(state='dead', reason='mayhem mode')
        return up
    real = curve.real_sol / LAMPORTS
    community = real - (token.get('dev_sol') or 0)
    previous, previous_at = token.get('real_sol'), token.get('checked_at')
    if previous is None or not previous_at:
        minutes = max((now - (token.get('created_at') or now)) / 60, .5)
        rate = max(0.0, community) / minutes
        stalls = 0
    else:
        rate = max(0.0, real - previous) / max((now - previous_at) / 60, .25)
        stalls = (token.get('stalls') or 0) + 1 if real <= previous + .05 else 0
    peak_real = max(token.get('peak_real_sol') or 0, real)
    mcap = curve.mcap_sol or 0
    up.update(real_sol=real, peak_real_sol=peak_real, traction=rate, stalls=stalls,
              progress=curve.progress * 100, mcap_sol=mcap,
              first_mcap_sol=token.get('first_mcap_sol') or mcap,
              peak_mcap_sol=max(token.get('peak_mcap_sol') or 0, mcap))
    if curve.complete:
        up.update(state='alive', reason='graduated, waiting for migration')
    elif peak_real >= rules.alive_min_sol and real < peak_real * rules.dump_ratio:
        up.update(state='dead', reason='dumped')
    elif community >= rules.alive_min_sol:
        if stalls >= rules.max_stalls:
            up.update(state='dead', reason='stalled')
        else:
            up['state'] = 'alive'
    elif checks >= 2:
        up.update(state='dead', reason='no inflow')
    else:
        up['state'] = 'fresh'
    return up


def blocker(token: dict, now: float, rules: ReservoirRules, launches: int) -> str | None:
    """Why an alive token cannot be promoted now (None = it can)."""
    if token.get('state') != 'alive':
        return 'not alive'
    if (token.get('cooldown_until') or 0) > now:
        return 'cooling down'
    if (token.get('promotions') or 0) >= rules.max_promotions:
        return 'watched enough'
    if rules.max_creator_launches and launches > rules.max_creator_launches:
        return 'serial creator'
    buy = dev_buy_pct(token)
    if buy is not None and buy > rules.max_dev_buy_pct:
        return 'dev bought too much'
    if token.get('stage') == 'migrated':
        if now - (token.get('migrated_at') or 0) > rules.migrated_minutes * 60:
            return 'migration too old'
        return None
    if (token.get('progress') or 0) > rules.max_progress:
        return 'late on curve'
    if now - (token.get('checked_at') or 0) > rules.stale_after:
        return 'stale reading'
    community = (token.get('real_sol') or 0) - (token.get('dev_sol') or 0)
    if community < rules.promote_min_sol:
        return 'little inflow'
    if (token.get('traction') or 0) < rules.promote_min_rate:
        return 'slow inflow'
    return None


def candidates(tokens: list[dict], now: float, rules: ReservoirRules,
               launches_of: Callable[[str | None], int]) -> list[dict]:
    """Promotable tokens: fresh migrations first, then bonding tokens by inflow rate."""
    ready = [t for t in tokens if blocker(t, now, rules, launches_of(t.get('creator'))) is None]
    migrated = sorted((t for t in ready if t.get('stage') == 'migrated'),
                      key=lambda t: -(t.get('migrated_at') or 0))
    bonding = sorted((t for t in ready if t.get('stage') != 'migrated'),
                     key=lambda t: -(t.get('traction') or 0))
    return migrated + bonding
