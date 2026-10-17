"""Telegram messages (Indonesian) and delivery that never sends a call twice."""
from __future__ import annotations

import asyncio
import html
import json
from urllib.parse import quote

import aiohttp

from .flow import Flow
from .quality import Assessment, grade

def usd(value: float) -> str:
    if value >= 1e6:
        return f'${value / 1e6:.2f}M'
    if value >= 1e3:
        return f'${value / 1e3:.1f}K'
    return f'${value:.0f}'


def market_cap(mcap_sol: float | None, sol_usd: float | None) -> str:
    if not mcap_sol:
        return 'n/a'
    if sol_usd:
        return f'<b>{usd(mcap_sol * sol_usd)}</b> ({mcap_sol:,.0f} SOL)'
    return f'<b>{mcap_sol:,.1f} SOL</b>'


def unit_price(price_sol: float | None, sol_usd: float | None) -> str:
    if not price_sol:
        return 'n/a'
    if sol_usd:
        return f'${price_sol * sol_usd:.4g}'
    return f'{price_sol:.4g} SOL'


def duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f'{seconds} dtk'
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes} mnt'
    return f'{minutes // 60} jam {minutes % 60} mnt'


def links(mint: str) -> str:
    m = quote(mint, safe='')
    return json.dumps({'inline_keyboard': [
        [{'text': '📊 Chart', 'url': f'https://dexscreener.com/solana/{m}'},
         {'text': '⚡ GMGN', 'url': f'https://gmgn.ai/sol/token/{m}'}],
        [{'text': '🚀 Pump.fun', 'url': f'https://pump.fun/coin/{m}'},
         {'text': '🔎 Solscan', 'url': f'https://solscan.io/token/{m}'}],
    ]})


def call_text(token: dict, flow: Flow, a: Assessment, sol_usd: float | None, now: float,
              window: int) -> str:
    e = html.escape
    symbol = e(token.get('symbol') or 'TOKEN')
    name = e(token.get('name') or symbol)
    lines = [f'⚡ <b>SOLTER CALL</b> · Grade <b>{grade(a.score)}</b> · {a.score}/100',
             f'<b>{name}</b> (${symbol})', '']
    age = duration(now - (token.get('created_at') or now))
    if token.get('stage') == 'migrated':
        liquidity = f' · likuiditas {usd(a.liquidity_usd)}' if a.liquidity_usd else ''
        lines.append(f'🔁 PumpSwap{liquidity} · terpantau {age}')
    else:
        lines.append(f'🧪 Bonding curve {a.progress or 0:.0f}% · umur {age}')
    lines.append(f'💰 MC {market_cap(a.mcap_sol, sol_usd)} · harga {unit_price(a.price_sol, sol_usd)}')
    run = f' · harga {flow.run * 100:+.0f}%' if flow.run is not None else ''
    lines.append(f'📈 Net inflow <b>{flow.net_sol:+.1f} SOL</b> · vol {flow.volume_sol:.1f} SOL '
                 f'/{duration(window)}{run}')
    lines.append(f'👥 {flow.buyers} pembeli · {flow.recent_buyers} aktif 45 dtk · '
                 f'burst {flow.burst}/20 dtk')
    if a.holders:
        dev = f'{a.holders.dev:.1f}%' if a.holders.dev is not None else 'n/a'
        lines.append(f'🐳 Top10 {a.holders.top10:.1f}% · terbesar {a.holders.largest:.1f}% · dev {dev}')
    creator = f'launch ke-{a.launches}' if a.launches else 'creator belum dikenal'
    bought = f' · beli awal {a.dev_buy_pct:.1f}%' if a.dev_buy_pct is not None else ''
    lines.append(f'👨‍💻 Dev: {creator}{bought} · belum terlihat jual')
    social = [f'<a href="{e(url, quote=True)}">{label}</a>' for label, url in
              (('X', a.socials.twitter), ('Telegram', a.socials.telegram), ('Web', a.socials.website))
              if url]
    lines.append('🔗 ' + (' · '.join(social) if social else 'Tanpa sosial'))
    lines.append('🛡️ Mint & freeze authority dicabut')
    lines += ['', f'<code>{e(token["mint"])}</code>',
              '<i>Bukan saran finansial. Memecoin berisiko tinggi, DYOR.</i>']
    return '\n'.join(lines)


def market_alert_text(token: dict, strength: int, now: float,
                      socials: dict[str, str] | None = None) -> str:
    """Compact, copyable and explicit that market indicators are not safety checks."""
    from .market_media import public_https
    e = html.escape
    symbol = e(str(token.get('symbol') or '?')[:32])
    name = e(str(token.get('name') or token.get('symbol') or 'Token')[:80])
    buys, sells = token['market_buys'], token['market_sells']
    vol, liq = token['market_volume_usd'], token['market_liquidity_usd']
    change, mcap = token['market_change_pct'], token['market_mcap_usd']
    pair_seconds = max(0, now - (token.get('pair_created_at') or now))
    if pair_seconds >= 86400:
        age = f'{int(pair_seconds // 86400)} hari {int(pair_seconds % 86400 // 3600)} jam'
    else:
        age = duration(pair_seconds)
    ratio = buys / max(1, sells)
    lines = [
        '⚡ <b>SOLTER MOMENTUM ALERT</b>',
        f'<b>{name}</b>  ·  ${symbol}',
        f'🔁 PumpSwap  •  Pool {age}', '',
        '📈 <b>MOMENTUM 5 MENIT</b>',
        f'• Harga <b>{change:+.1f}%</b>   |   MC <b>{usd(mcap)}</b>',
        f'• Volume <b>{usd(vol)}</b>   |   Likuiditas <b>{usd(liq)}</b>',
        f'• Beli <b>{buys}</b>   |   Jual <b>{sells}</b>   |   Rasio <b>{ratio:.1f}×</b>',
        f'• Momentum <b>{strength}/100</b> <i>(bukan skor keamanan)</i>',
        '<i>Beli/jual = jumlah transaksi, bukan wallet unik.</i>', '',
        '🛡️ <b>STATUS KEAMANAN</b>',
        '⚠️ <b>BELUM DIVERIFIKASI ON-CHAIN</b>',
        'Holder, dev, mint/freeze &amp; wash trading belum diperiksa.',
    ]
    social_links = []
    for label in ('X', 'Telegram', 'Website'):
        url = public_https((socials or {}).get(label))
        if url:
            social_links.append(f'<a href="{e(url, quote=True)}">{label}</a>')
    if social_links:
        lines += ['', '🔗 ' + '  ·  '.join(social_links)]
    lines += ['', '📋 <b>CONTRACT ADDRESS</b>', f'<code>{e(token["mint"])}</code>',
              '<i>Snapshot DEX dapat terlambat. Bukan jaminan profit atau rekomendasi beli.</i>']
    return '\n'.join(lines)


def milestone_text(symbol: str, multiple: float, mcap_sol: float | None, sol_usd: float | None,
                   minutes: float) -> str:
    return (f'🚀 <b>{html.escape(symbol or "TOKEN")}</b> {multiple:.1f}x sejak call · '
            f'MC {market_cap(mcap_sol, sol_usd)} · {duration(minutes * 60)}')


def dev_sold_text(symbol: str) -> str:
    return f'⚠️ Dev <b>{html.escape(symbol or "TOKEN")}</b> menjual setelah call. Hati-hati.'


class Telegram:
    def __init__(self, token: str, chat: str, session):
        self.token = token
        self.chat = chat
        self.session = session

    async def send(self, method: str, payload: dict) -> tuple[str, int | None]:
        """('sent', message_id) | ('rejected', None) | ('uncertain', None). Never retried here."""
        try:
            async with self.session.post(f'https://api.telegram.org/bot{self.token}/{method}',
                                         data=payload, timeout=aiohttp.ClientTimeout(total=15)) as r:
                body = await r.json(content_type=None)
                if r.status == 200 and isinstance(body, dict) and body.get('ok'):
                    result = body.get('result') or {}
                    return 'sent', result.get('message_id') if isinstance(result, dict) else None
                if r.status == 400:
                    return 'rejected', None
                return 'uncertain', None
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return 'uncertain', None

    async def call(self, text: str, mint: str, image: str | None) -> tuple[str, int | None]:
        """One sendMessage; the token image, if any, is only its link preview, so a slow image
        host can never hold the call back. A rejected message (nothing was posted) is sent once
        more without the preview; an uncertain result (timeout) is not retried, so a call is
        never duplicated."""
        common = {'chat_id': self.chat, 'parse_mode': 'HTML', 'reply_markup': links(mint), 'text': text}
        plain = json.dumps({'is_disabled': True})
        preview = (json.dumps({'url': image, 'prefer_large_media': True, 'show_above_text': True})
                   if image else plain)
        state, message_id = await self.send('sendMessage', {**common, 'link_preview_options': preview})
        if state == 'rejected' and preview != plain:
            state, message_id = await self.send('sendMessage', {**common, 'link_preview_options': plain})
        return state, message_id

    async def reply(self, text: str, message_id: int | None) -> str:
        payload = {'chat_id': self.chat, 'parse_mode': 'HTML', 'text': text,
                   'link_preview_options': json.dumps({'is_disabled': True})}
        if message_id:
            payload['reply_parameters'] = json.dumps({'message_id': message_id,
                                                      'allow_sending_without_reply': True})
        state, _ = await self.send('sendMessage', payload)
        return state
