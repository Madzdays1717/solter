"""Who holds the token and who made it: the checks between good flow and a call.

The score is a documented, deterministic ranking of setup quality, not a
prediction. `python bot.py report` shows outcomes per grade so the
thresholds can be tuned from this installation's own calls.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .flow import Flow


@dataclass(frozen=True)
class QualityRules:
    max_top10_pct: float = 30.0      # 10 largest holders, pool/curve vault excluded
    max_holder_pct: float = 10.0     # largest single holder
    max_dev_pct: float = 10.0        # creator's current balance
    max_dev_buy_pct: float = 15.0    # creator's buy at launch
    max_creator_launches: int = 3    # launches seen from this creator (this one included); 0 = off
    max_progress: float = 85.0       # bonding curve progress, percent
    require_socials: bool = False
    min_score: int = 0
    min_mcap_usd: float = 0.0        # market cap window in USD (0 = no limit)
    max_mcap_usd: float = 0.0


@dataclass(frozen=True)
class Holders:
    top10: float            # percent of supply
    largest: float
    dev: float | None       # None when unknown


def holder_stats(accounts, exclude: set[str], supply_raw: int, dev_raw: int | None) -> Holders | None:
    """From getTokenLargestAccounts rows (address, raw amount)."""
    if not supply_raw or supply_raw <= 0:
        return None
    amounts = []
    for row in accounts or []:
        if not isinstance(row, dict) or row.get('address') in exclude:
            continue
        try:
            amounts.append(int(row['amount']))
        except (KeyError, TypeError, ValueError):
            continue
    amounts.sort(reverse=True)
    top = amounts[:10]
    return Holders(100 * sum(top) / supply_raw, 100 * (top[0] if top else 0) / supply_raw,
                   None if dev_raw is None else 100 * dev_raw / supply_raw)


@dataclass(frozen=True)
class Socials:
    twitter: str | None = None
    telegram: str | None = None
    website: str | None = None
    image: str | None = None

    @property
    def count(self) -> int:
        return sum(1 for x in (self.twitter, self.telegram, self.website) if x)


_SAFE_URL = re.compile(r'^https://[^\s"\'<>]{4,300}$')


def safe_url(value, hosts: tuple[str, ...] | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith('ipfs://'):
        value = 'https://ipfs.io/ipfs/' + value[len('ipfs://'):]
    if not _SAFE_URL.match(value):
        return None
    host = (urlsplit(value).hostname or '').lower()
    if not host:
        return None
    if hosts and not any(host == h or host.endswith('.' + h) for h in hosts):
        return None
    return value


def socials_from(meta) -> Socials:
    """Links from a Pump.fun metadata JSON (the token's `uri`)."""
    if not isinstance(meta, dict):
        return Socials()
    return Socials(safe_url(meta.get('twitter'), ('x.com', 'twitter.com')),
                   safe_url(meta.get('telegram'), ('t.me', 'telegram.me')),
                   safe_url(meta.get('website')),
                   safe_url(meta.get('image')))


@dataclass(frozen=True)
class Assessment:
    """Everything the deep checks learned about a candidate; `reason` set = no call."""
    reason: str | None
    price_sol: float | None = None
    mcap_sol: float | None = None
    progress: float | None = None
    holders: Holders | None = None
    socials: Socials = Socials()
    launches: int = 0
    dev_buy_pct: float | None = None
    score: int = 0
    liquidity_usd: float | None = None
    creator: str | None = None
    image: str | None = None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def score(flow: Flow, holders: Holders, socials: Socials, launches: int) -> int:
    """0-100: flow 40, distribution 30, context 30. Each part is linear between stated anchors."""
    s = 15 * _clamp(math.log1p(max(0.0, flow.net_sol)) / math.log1p(20))   # net SOL, 20+ = full
    s += 15 * _clamp((flow.buyers - 5) / 15)                                 # 6 .. 20 buyers
    s += 10 * _clamp(flow.recent_buyers / 5)                                 # active in the last 45s
    s += 15 * _clamp((30 - holders.top10) / 20)                              # top10 30% -> 0, 10% -> full
    s += 5 * _clamp((10 - holders.largest) / 8)                              # largest 10% -> 0, 2% -> full
    s += 5 if holders.dev is None else 10 * _clamp((10 - holders.dev) / 10)  # dev 10% -> 0, 0% -> full
    s += 10 * _clamp(socials.count / 3)
    s += {1: 10, 2: 5}.get(launches, 0) if launches > 0 else 5               # unknown creator: half
    s += 5 * (1 - _clamp(flow.sell_ratio / .6))
    s += 5 * (1 - _clamp(flow.bundle_share / .5))
    return round(_clamp(s, 0, 100))


def grade(points: int) -> str:
    return 'A' if points >= 75 else 'B' if points >= 60 else 'C'


def market_cap_reason(rules: QualityRules, mcap_sol: float | None, sol_usd: float | None) -> str | None:
    """Outside the USD market-cap window? Unknown SOL price or market cap: the window is not applied."""
    if not mcap_sol or not sol_usd:
        return None
    usd = mcap_sol * sol_usd
    if rules.min_mcap_usd and usd < rules.min_mcap_usd:
        return 'mcap too low'
    if rules.max_mcap_usd and usd > rules.max_mcap_usd:
        return 'mcap too high'
    return None


def quality_reason(rules: QualityRules, holders: Holders | None, socials: Socials,
                   launches: int, dev_buy_pct: float | None) -> str | None:
    if holders is None:
        return 'holders unknown'
    if holders.top10 > rules.max_top10_pct:
        return 'top holders concentrated'
    if holders.largest > rules.max_holder_pct:
        return 'whale holder'
    if holders.dev is not None and holders.dev > rules.max_dev_pct:
        return 'dev holds too much'
    if dev_buy_pct is not None and dev_buy_pct > rules.max_dev_buy_pct:
        return 'dev bought too much'
    if rules.max_creator_launches and launches > rules.max_creator_launches:
        return 'serial creator'
    if rules.require_socials and not socials.count:
        return 'no socials'
    return None
