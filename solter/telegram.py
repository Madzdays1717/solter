"""Telegram delivery and message presentation."""

import asyncio
import html
import json
import logging
from urllib.parse import urlparse

import aiohttp

from .momentum import Signal

LOG = logging.getLogger('solter.telegram')


def format_signal(signal: Signal, sol_usd: float) -> str:
    if signal.market_cap_sol <= 0:
        cap = 'Belum tersedia'
    elif sol_usd > 0:
        cap_usd = signal.market_cap_sol * sol_usd
        cap = f'${cap_usd:,.2f}' if cap_usd < 1 else f'${cap_usd:,.0f}'
    else:
        cap = f'{signal.market_cap_sol:,.4f} SOL (USD belum tersedia)'
    volume_sol = signal.buy_sol + signal.sell_sol
    volume = (f'${volume_sol * sol_usd:,.0f}' if sol_usd > 0
              else f'{volume_sol:.2f} SOL')
    risk = signal.risk
    share = (f'{risk.buyer_concentration_pct:.0f}%'
             if risk.buyer_concentration_pct is not None else 'Tidak tersedia')
    developer = (f'Beli {risk.creator_bought_sol:.2f} / jual {risk.creator_sold_sol:.2f} SOL'
                 if risk.creator_bought_sol or risk.creator_sold_sol else 'Belum teramati')
    flags = '\n'.join(f'• {html.escape(flag)}' for flag in risk.warnings)
    flags = f'\n⚠️ <b>Perhatian</b>\n{flags}' if flags else ''
    volatility = '\n⚠️ Lonjakan harga tinggi' if signal.warning else ''

    return (
        '⚡ <b>SOLTER · EARLY MOMENTUM</b>\n\n'
        f'<b>{html.escape(signal.name)} (${html.escape(signal.symbol)})</b>\n'
        f'📍 {html.escape(signal.phase)} · Usia {signal.age_seconds / 60:.1f} menit\n\n'
        f'📈 <b>Harga 2 menit: {signal.change_pct:+.1f}%</b>\n'
        f'• MC: <b>{cap}</b>\n'
        f'• Volume terpantau: <b>{volume}</b>\n'
        f'• Transaksi buy/sell: <b>{signal.buys}/{signal.sells}</b>\n'
        f'• Wallet pembeli teramati: <b>{signal.buyers}</b>\n'
        f'• Net buy: <b>{signal.buy_sol - signal.sell_sol:+.2f} SOL</b>\n'
        f'• Cadangan kurva/pool: <b>{signal.pool_sol:,.2f} SOL</b>\n'
        f'• Skor momentum: <b>{signal.score}/100</b>\n'
        f'{volatility}\n\n'
        '🛡️ <b>RISIKO TRANSAKSI</b>\n'
        f'• Top 3 pembeli (porsi volume): <b>{share}</b>\n'
        f'• Aktivitas developer: <b>{developer}</b>\n'
        f'• Pembeli 30 detik awal: <b>{risk.early_buyers}</b>\n'
        f'• Transaksi multi-wallet: <b>{risk.shared_transaction_wallets}</b>'
        f'{flags}\n\n'
        '<i>Metrik wallet berdasarkan trade yang terpantau, bukan distribusi holder. '
        'Token belum dinyatakan aman. Volume dapat dimanipulasi.</i>\n\n'
        '<b>Contract Address</b>\n'
        f'<code>{html.escape(signal.mint)}</code>\n'
        '<i>Alert analitis, bukan jaminan profit.</i>'
    )


def links(mint: str) -> str:
    return json.dumps({'inline_keyboard': [[
        {'text': '🚀 Pump.fun', 'url': f'https://pump.fun/coin/{mint}'},
        {'text': '🔎 Solscan', 'url': f'https://solscan.io/token/{mint}'},
    ]]})


def image_url(url: str) -> str | None:
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    if (parsed.scheme == 'https' and parsed.hostname
            and not parsed.username and not parsed.password):
        return url[:1024]
    return None


class Telegram:
    def __init__(self, session: aiohttp.ClientSession, token: str, chat_id: str):
        self.session = session
        self.url = f'https://api.telegram.org/bot{token}/sendMessage'
        self.chat_id = chat_id
        self.lock = asyncio.Lock()
        self.last_send = 0.0

    async def send(self, signal: Signal, sol_usd: float) -> tuple[str, int | None]:
        body = {
            'chat_id': self.chat_id,
            'text': format_signal(signal, sol_usd),
            'parse_mode': 'HTML',
            'reply_markup': links(signal.mint),
        }
        preview = image_url(signal.image)
        if preview:
            body['link_preview_options'] = json.dumps({
                'url': preview, 'prefer_large_media': True, 'show_above_text': True,
            })
        else:
            body['link_preview_options'] = json.dumps({'is_disabled': True})
        async with self.lock:
            pause = max(0, 0.8 - (asyncio.get_running_loop().time() - self.last_send))
            if pause:
                await asyncio.sleep(pause)
            self.last_send = asyncio.get_running_loop().time()
            for _ in range(3):
                try:
                    async with self.session.post(self.url, data=body,
                        timeout=aiohttp.ClientTimeout(total=12)) as resp:
                        result = await resp.json(content_type=None)
                        if resp.status == 200 and result.get('ok'):
                            return 'sent', (result.get('result') or {}).get('message_id')
                        if resp.status == 429:
                            wait = min(20, max(1, int((result.get('parameters') or {}).get('retry_after', 3))))
                            LOG.warning('Telegram pacing: %ss', wait)
                            await asyncio.sleep(wait)
                            continue
                        if preview:
                            body['link_preview_options'] = json.dumps({'is_disabled': True})
                            preview = None
                            continue
                        LOG.warning('Telegram rejected alert: HTTP %s', resp.status)
                        return 'failed', None
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    LOG.warning('Telegram delivery status uncertain: %s', type(exc).__name__)
                    return 'uncertain', None
            return 'failed', None
