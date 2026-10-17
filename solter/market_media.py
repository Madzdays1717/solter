"""Optional DexScreener artwork/links for market alerts, never a new API request.

All metadata is untrusted input. Artwork should never hold up signal decisions.
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from .chain import valid_address


def public_https(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    if any(ord(c) < 33 or ord(c) == 127 for c in value):
        return None
    try:
        url = urlsplit(value)
        host = url.hostname
        if url.scheme.lower() != 'https' or not host or url.username or url.password:
            return None
        if host.lower() == 'localhost' or host.lower().endswith(('.local', '.internal')):
            return None
        try:
            address = ipaddress.ip_address(host)
            if not address.is_global:
                return None
        except ValueError:
            pass  # public-looking domain; Telegram ultimately fetches the preview
        if url.port is not None and url.port != 443:
            return None
    except ValueError:
        return None
    return value


def _socials(items: object) -> dict[str, str]:
    social: dict[str, str] = {}
    if not isinstance(items, list):
        return social
    for item in items[:12]:
        if not isinstance(item, dict):
            continue
        url = public_https(item.get('url'))
        kind = str(item.get('type') or item.get('label') or '').lower()
        if not url:
            continue
        host = (urlsplit(url).hostname or '').lower()
        if kind in ('twitter', 'x') and host in ('x.com', 'www.x.com', 'twitter.com', 'www.twitter.com'):
            social.setdefault('X', url)
        elif kind in ('telegram', 'tg') and host in ('t.me', 'telegram.me', 'www.t.me'):
            social.setdefault('Telegram', url)
        elif kind in ('website', 'web', 'official website'):
            social.setdefault('Website', url)
    return social


def extract_pair_media(pair: dict) -> dict:
    info = pair.get('info') or {}
    if not isinstance(info, dict):
        return {}
    social = _socials(info.get('socials'))
    if isinstance(info.get('websites'), list):
        for site in info['websites'][:6]:
            if isinstance(site, dict):
                url = public_https(site.get('url'))
                if url:
                    social.setdefault('Website', url)
                    break
    return {'header': public_https(info.get('header')),
            'icon': public_https(info.get('imageUrl')),
            'socials': social}


def extract_profile_media(item: object) -> tuple[str, dict] | None:
    if not isinstance(item, dict) or item.get('chainId') != 'solana':
        return None
    mint = item.get('tokenAddress')
    if not valid_address(mint):
        return None
    return mint, {'header': public_https(item.get('header')),
                  'icon': public_https(item.get('icon')),
                  'socials': _socials(item.get('links'))}


def combine_media(pair: dict, cached: dict | None) -> dict:
    direct = extract_pair_media(pair)
    cached = cached or {}
    socials = {**direct.get('socials', {}), **cached.get('socials', {})}
    # Real profile banner preferred to a square token icon. No extra HTTP request.
    header = cached.get('header') or direct.get('header')
    icon = direct.get('icon') or cached.get('icon')
    return {'image': header or icon, 'image_kind': 'banner' if header else 'icon',
            'socials': socials}
