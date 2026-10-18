"""Token identity, social links, and optional market snapshots."""

from dataclasses import dataclass
from urllib.parse import urlparse
import ipaddress

from .events import number


@dataclass(frozen=True)
class Profile:
    name: str = ''
    symbol: str = ''
    image: str = ''
    banner: str = ''
    twitter: str = ''
    telegram: str = ''
    website: str = ''
    created_at: float = 0.0
    supply: float = 0.0
    market_cap_usd: float = 0.0
    graduated: bool = False


def public_url(value: object, *, sites: tuple[str, ...] = ()) -> str:
    """Keep clickable links restricted to public HTTPS destinations."""
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if not value or len(value) > 512 or any(ord(char) < 32 for char in value):
        return ''
    if '://' not in value:
        value = 'https://' + value
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or '').lower().rstrip('.')
        if (parsed.scheme != 'https' or not host or '.' not in host
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or host.endswith(('.local', '.internal'))
                or host in ('localhost', '0.0.0.0')
                or any(char not in 'abcdefghijklmnopqrstuvwxyz0123456789.-' for char in host)):
            return ''
        try:
            ipaddress.ip_address(host)
            return ''
        except ValueError:
            pass
        if sites and not any(host == site or host.endswith('.' + site) for site in sites):
            return ''
        return value
    except ValueError:
        return ''


def metadata_image(data: dict, *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, dict):
            value = value.get('url') or value.get('uri')
        url = public_url(value)
        if url:
            return url
    return ''


def parse_profile(data: object, *, pump: bool) -> Profile | None:
    if not isinstance(data, dict):
        return None
    launched = number(data.get('created_timestamp' if pump else 'created_at'))
    if launched > 1e11:
        launched /= 1000
    supply = number(data.get('total_supply'))
    if pump and supply > 1e12:
        # Pump's frontend response may use raw mint units rather than UI units.
        decimals = int(number(data.get('decimals'))) if 'decimals' in data else 6
        if 0 <= decimals <= 12:
            supply /= 10 ** decimals
    return Profile(
        name=str(data.get('name') or '')[:80],
        symbol=str(data.get('symbol') or '')[:28],
        image=metadata_image(data, 'image_uri', 'image', 'imageUrl', 'image_url', 'logo'),
        banner=metadata_image(data, 'banner_uri', 'banner_url', 'bannerUrl', 'banner',
                              'headerImage', 'header_image'),
        twitter=public_url(data.get('twitter'), sites=('x.com', 'twitter.com')),
        telegram=public_url(data.get('telegram'), sites=('t.me', 'telegram.me')),
        website=public_url(data.get('website')),
        created_at=launched,
        supply=supply,
        market_cap_usd=number(data.get('usd_market_cap')) if pump else 0.0,
        graduated=bool(data.get('complete' if pump else 'graduated')),
    )
