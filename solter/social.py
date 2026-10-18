"""Summarize public mentions of a token without inferring motives or ownership.

The collector only accepts explicit contract-address mentions. This sacrifices
coverage in exchange for avoiding false matches against common meme tickers.
"""

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import logging
import re
import time

import aiohttp

from .addresses import valid_address

LOG = logging.getLogger('solter.social')
SEARCH = 'https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts'
FALLBACK_SEARCH = 'https://api.bsky.app/xrpc/app.bsky.feed.searchPosts'
WINDOW = 900


@dataclass(frozen=True)
class Mention:
    uri: str
    author: str
    created_at: float
    fingerprint: str
    text: str


@dataclass(frozen=True)
class SocialReport:
    status: str
    posts: int
    authors: int
    recent: int
    earlier: int
    similar_pct: float
    topic: str
    observed_at: float
    source: str = 'Bluesky'


def _timestamp(value: object) -> float:
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).timestamp()
    except ValueError:
        return 0.0


def parse_mentions(payload: object, mint: str, now: float) -> list[Mention]:
    if not isinstance(payload, dict) or not isinstance(payload.get('posts'), list):
        return []
    found = []
    for post in payload['posts'][:60]:
        if not isinstance(post, dict):
            continue
        record = post.get('record') or {}
        author = post.get('author') or {}
        if not isinstance(record, dict) or not isinstance(author, dict):
            continue
        content = record.get('text') or ''
        stamp = _timestamp(record.get('createdAt') or post.get('indexedAt'))
        if (not isinstance(content, str) or mint not in content
                or not now - WINDOW <= stamp <= now + 30):
            continue
        uri = post.get('uri')
        did = author.get('did')
        if not isinstance(uri, str) or not isinstance(did, str):
            continue
        # Normalize links and contract addresses to identify repetitive promotions.
        canonical = re.sub(r'https?://\S+', ' URL ', content.lower())
        canonical = canonical.replace(mint.lower(), ' CA ')
        canonical = ' '.join(re.findall(r'[\w#]+', canonical))[:300]
        if not canonical:
            continue
        meaningful = [word for word in canonical.split() if word not in ('ca', 'url')]
        if len(meaningful) < 3:
            # A post containing only a contract address is not evidence of a
            # coordinated copywriting campaign.
            canonical += f' {uri}'
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:20]
        found.append(Mention(uri, did, stamp, digest, content[:500]))
    return found


def summarize(mentions: list[Mention], now: float) -> SocialReport | None:
    latest = {item.uri: item for item in mentions if now - WINDOW <= item.created_at <= now + 30}
    if not latest:
        return None
    rows = list(latest.values())
    authors = len({p.author for p in rows})
    recent = sum(p.created_at >= now - 300 for p in rows)
    earlier = sum(now - 600 <= p.created_at < now - 300 for p in rows)
    fingerprints = Counter(p.fingerprint for p in rows)
    similar_pct = 100 * sum(count for count in fingerprints.values() if count > 1) / len(rows)
    if len(rows) >= 6 and similar_pct >= 65:
        state = 'Pola promosi serupa'
    elif authors >= 5 and recent >= 5 and earlier >= 2 and recent >= earlier * 1.5:
        state = 'Perhatian meningkat'
    elif earlier >= 5 and recent * 2 <= earlier:
        state = 'Perhatian mereda'
    else:
        state = 'Pembicaraan terpantau'
    # Hashtags are supporting context, not evidence that a meme is trending globally.
    tags: dict[str, set[str]] = {}
    for post in rows:
        for tag in set(re.findall(r'(?<!\w)#([\w]{3,30})', post.text.lower())):
            if tag not in ('solana', 'pumpfun', 'pump', 'crypto', 'memecoin'):
                tags.setdefault(tag, set()).add(post.author)
    good = sorted(((len(who), tag) for tag, who in tags.items() if len(who) >= 2), reverse=True)
    topic = '#' + good[0][1] if good else ''
    return SocialReport(state, len(rows), authors, recent, earlier,
                        round(similar_pct, 1), topic, now)


class SocialMonitor:
    """Bounded, best-effort search of token mentions from a public service."""

    def __init__(self, session: aiohttp.ClientSession, on_report):
        self.session = session
        self.on_report = on_report
        self.pending: dict[str, tuple[float, float]] = {}
        self.checked: dict[str, float] = {}
        self.mentions: dict[str, dict[str, Mention]] = {}
        self.reports: dict[str, SocialReport] = {}
        self.unavailable_until = 0.0
        self.queries = 0
        self.failures = 0

    def request(self, mint: str, *, priority: float = 1.0) -> None:
        now = time.time()
        if not valid_address(mint) or now - self.checked.get(mint, 0) < 60:
            return
        if mint in self.pending:
            previous, queued = self.pending[mint]
            self.pending[mint] = (max(priority, previous), queued)
            return
        if len(self.pending) >= 120:
            weakest = min(self.pending, key=lambda key: self.pending[key][0])
            if self.pending[weakest][0] >= priority:
                return
            self.pending.pop(weakest)
        self.pending[mint] = (priority, now)

    async def _search(self, mint: str) -> object | None:
        for url in (SEARCH, FALLBACK_SEARCH):
            try:
                async with self.session.get(url,
                        params={'q': mint, 'sort': 'latest', 'limit': '50'},
                        timeout=aiohttp.ClientTimeout(total=4)) as response:
                    if response.status == 200:
                        return await response.json(content_type=None)
                    if response.status == 429:
                        self.unavailable_until = time.time() + min(
                            600, max(90, int(response.headers.get('Retry-After', '120'))))
                        return None
                    if response.status not in (403, 404):
                        break
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                continue
        self.unavailable_until = time.time() + 180
        self.failures += 1
        return None

    async def run(self):
        while True:
            now = time.time()
            if now < self.unavailable_until or not self.pending:
                await asyncio.sleep(1)
                continue
            # Do not spend public API quota on a stale request.
            self.pending = {key: value for key, value in self.pending.items()
                            if now - value[1] < 90}
            if not self.pending:
                continue
            mint = max(self.pending, key=lambda key: self.pending[key][0])
            self.pending.pop(mint, None)
            self.checked[mint] = now
            self.queries += 1
            payload = await self._search(mint)
            if payload is not None:
                mentions = self.mentions.setdefault(mint, {})
                for mention in parse_mentions(payload, mint, time.time()):
                    mentions[mention.uri] = mention
                now = time.time()
                self.mentions[mint] = {uri: item for uri, item in mentions.items()
                                       if item.created_at >= now - WINDOW}
                report = summarize(list(self.mentions[mint].values()), now)
                if report:
                    previous = self.reports.get(mint)
                    self.reports[mint] = report
                    if previous != report:
                        self.on_report(mint, report)
            # The work queue must not monopolize a free public API.
            await asyncio.sleep(10)

    def prune(self, active: set[str]) -> None:
        for mapping in (self.pending, self.checked, self.mentions, self.reports):
            for mint in set(mapping) - active:
                del mapping[mint]
