"""Fast, human-readable Telegram alert; optional banner never gets a fetch."""

import asyncio
import html
import ipaddress
import json
import logging
from urllib.parse import urlsplit

import aiohttp

from .signals import Decision, MarketSnapshot

LOG = logging.getLogger('solter.telegram')


def money(value: float) -> str:
    if value >= 1_000_000:
        return f'${value/1_000_000:,.2f}M'
    if value >= 1_000:
        return f'${value/1_000:,.1f}K'
    return f'${value:,.0f}'


def public_image(value: object) -> str | None:
    """Accept safe public image URLs; Telegram, not Solter, downloads artwork."""
    if not isinstance(value, str) or len(value) > 1600 or any(ord(c) < 33 for c in value):
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        if parts.scheme != 'https' or not host or parts.username or parts.password:
            return None
        if host == 'localhost' or host.endswith(('.local', '.internal')):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        if parts.port is not None and parts.port != 443:
            return None
    except ValueError:
        return None
    return value


def alert_text(s: MarketSnapshot, d: Decision) -> str:
    name, symbol = html.escape(s.name), html.escape(s.symbol)
    ratio = s.buys_5m / max(1, s.sells_5m)
    text = [
        f'⚡ <b>SOLTER · {html.escape(d.reason.upper())}</b>',
        f'<b>{name}</b>  ·  ${symbol}',
        f'🔁 {html.escape(s.dex)} · Pool {int(s.age_seconds // 60)} menit',
        '', '<b>📈 MOMENTUM 5 MENIT</b>',
        f'• Perubahan harga: <b>{s.change_5m:+.1f}%</b>',
        f'• Market cap: <b>{money(s.market_cap)}</b>',
        f'• Volume: <b>{money(s.volume_5m)}</b> · Likuiditas: <b>{money(s.liquidity)}</b>',
        f'• Beli / Jual: <b>{s.buys_5m} / {s.sells_5m}</b> · Rasio <b>{ratio:.2f}×</b>',
        f'• Skor momentum: <b>{d.strength}/100</b> (bukan skor keamanan)',
        '', '⚠️ <b>RISIKO: BELUM DIVERIFIKASI ON-CHAIN</b>',
        'Transaksi bukan jumlah wallet unik. Holder, dev, mint/freeze, dan wash trading belum diperiksa.',
    ]
    if d.high_risk_momentum:
        text += ['', '🚨 <b>Lonjakan sudah sangat tinggi:</b> risiko FOMO dan reversal.']
    text += ['', '<b>Contract Address</b>', f'<code>{html.escape(s.mint)}</code>',
             '<i>Data DEX dapat tertunda. Bukan sinyal aman atau jaminan profit.</i>']
    return '\n'.join(text)


def buttons(mint: str) -> str:
    return json.dumps({'inline_keyboard': [
        [{'text': '📊 Chart', 'url': f'https://dexscreener.com/solana/{mint}'},
         {'text': '⚡ GMGN', 'url': f'https://gmgn.ai/sol/token/{mint}'}],
        [{'text': '🚀 Pump.fun', 'url': f'https://pump.fun/coin/{mint}'},
         {'text': '🔎 Solscan', 'url': f'https://solscan.io/token/{mint}'}],
    ]})


class Telegram:
    def __init__(self, session: aiohttp.ClientSession, token: str, chat_id: str):
        self.session, self.token, self.chat_id = session, token, chat_id
        self.lock = asyncio.Lock()
        self.last_delivery = 0.0

    async def send(self, snapshot: MarketSnapshot, decision: Decision,
                   image: str | None) -> tuple[str, int | None]:
        if not self.token or not self.chat_id:
            return 'not_configured', None
        body = {
            'chat_id': self.chat_id,
            'text': alert_text(snapshot, decision),
            'parse_mode': 'HTML',
            'reply_markup': buttons(snapshot.mint),
        }
        preview = public_image(image)
        url = f'https://api.telegram.org/bot{self.token}/sendMessage'
        # Telegram throughput is independent from discovery/polling.
        async with self.lock:
            loop = asyncio.get_running_loop()
            pause = max(0, 1 - (loop.time() - self.last_delivery))
            if pause:
                await asyncio.sleep(pause)
            self.last_delivery = loop.time()
            if preview:
                body['link_preview_options'] = json.dumps({
                    'url': preview, 'prefer_large_media': True, 'show_above_text': True})
            else:
                body['link_preview_options'] = json.dumps({'is_disabled': True})
            for attempt in range(2):
                try:
                    async with self.session.post(url, data=body,
                            timeout=aiohttp.ClientTimeout(total=9)) as response:
                        payload = await response.json(content_type=None)
                        if response.status == 200 and payload.get('ok'):
                            msg = payload.get('result') or {}
                            return 'sent', msg.get('message_id')
                        if response.status == 429:
                            LOG.warning('Telegram rate limited an alert')
                            return 'rejected', None
                        if preview and attempt == 0:
                            # Invalid artwork preview must not suppress the text.
                            body['link_preview_options'] = json.dumps({'is_disabled': True})
                            preview = None
                            continue
                        LOG.warning('Telegram rejected alert (HTTP %s)', response.status)
                        return 'rejected', None
                except (asyncio.TimeoutError, aiohttp.ClientError) as error:
                    # Outcome is ambiguous: retrying risks duplicate messages.
                    LOG.warning('Telegram delivery uncertain: %s', type(error).__name__)
                    return 'uncertain', None
        return 'rejected', None
