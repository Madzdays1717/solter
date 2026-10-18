"""Pesan Telegram yang ringkas dan pengiriman yang tahan gangguan."""

import asyncio
import html
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import aiohttp

from .metadata import public_url
from .momentum import Signal

LOG = logging.getLogger('solter.telegram')
WIB = timezone(timedelta(hours=7))


def angka(value: float, digits: int = 0) -> str:
    """Format angka Indonesia tanpa mengubah nilai aslinya."""
    return f'{value:,.{digits}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def market_cap(signal: Signal, sol_usd: float) -> str:
    if (signal.reported_mc_usd > 0 and signal.reported_mc_at > 0
            and abs(signal.timestamp - signal.reported_mc_at) <= 20):
        return f'${angka(signal.reported_mc_usd)}'
    if signal.market_cap_sol > 0 and sol_usd > 0:
        return f'± ${angka(signal.market_cap_sol * sol_usd)}'
    if signal.market_cap_sol > 0:
        return f'± {angka(signal.market_cap_sol, 2)} SOL'
    return 'Belum tersedia'


def pesan(signal: Signal, sol_usd: float) -> str:
    phase = 'PUMPSWAP' if signal.phase == 'PumpSwap' else 'PUMP.FUN'
    volume_sol = signal.buy_sol + signal.sell_sol
    volume = (f'${angka(volume_sol * sol_usd)}' if sol_usd > 0
              else f'{angka(volume_sol, 2)} SOL')
    change = angka(signal.change_pct, 1)
    net = angka(signal.buy_sol - signal.sell_sol, 2)
    observed = datetime.fromtimestamp(signal.timestamp, tz=timezone.utc).astimezone(WIB)
    notes = []
    if signal.warning:
        notes.append('⚠️ Pergerakan harga sangat cepat')
    if signal.risk.creator_sold_sol >= .3:
        notes.append('⚠️ Penjualan pembuat token teramati')
    if (signal.risk.buyer_concentration_pct is not None
            and signal.risk.buyer_concentration_pct >= 80):
        notes.append('⚠️ Pembelian terkonsentrasi pada sedikit wallet')

    social = ''
    if (signal.social_status and signal.social_posts >= 3
            and signal.social_authors >= 2
            and abs(time.time() - signal.social_observed_at) < 180):
        social = (f'\n🌐 <b>Sosial (Bluesky):</b> {html.escape(signal.social_status)}\n'
                  f'   {signal.social_posts} posting · {signal.social_authors} akun teramati\n')
        if signal.social_status == 'Pola promosi serupa':
            social += '   ⚠️ Pola posting mirip; bukan bukti manipulasi.\n'
        if signal.social_topic:
            social += f'   Topik terkait: {html.escape(signal.social_topic)}\n'

    return (
        f'⚡ <b>SOLTER · {phase}</b>\n'
        f'<b>{html.escape(signal.name)} (${html.escape(signal.symbol)})</b>\n'
        f'⏱ Usia token: {angka(signal.age_seconds / 60, 1)} menit\n\n'
        f'💰 <b>Kapitalisasi pasar:</b> {market_cap(signal, sol_usd)}\n'
        f'📈 <b>Momentum:</b> Menguat (+{change.lstrip("+")}% sejak terpantau)\n'
        f'💹 <b>Arus beli bersih:</b> {net} SOL\n'
        f'💵 <b>Volume transaksi:</b> {volume}\n'
        + (f'💧 <b>Likuiditas:</b> ${angka(signal.liquidity_usd)}\n' if signal.liquidity_usd else '')
        + f'🟢 <b>Beli / Jual:</b> {signal.buys} / {signal.sells}\n'
        f'🕒 <b>Data terakhir:</b> {observed:%H:%M:%S} WIB\n'
        + ('\n'.join(notes) + '\n' if notes else '')
        + social
        + '\n📋 <b>Alamat kontrak</b>\n'
        f'<code>{html.escape(signal.mint)}</code>\n'
        '<i>Data kepemilikan dan keamanan token belum diverifikasi.</i>'
    )


def tombol(signal: Signal) -> str:
    mint = signal.mint
    rows = [
        [
            {'text': '📊 DexScreener', 'url': f'https://dexscreener.com/solana/{mint}'},
            {'text': '⚡ GMGN', 'url': f'https://gmgn.ai/sol/token/{mint}'},
        ],
        [
            {'text': '🚀 Pump.fun', 'url': f'https://pump.fun/coin/{mint}'},
            {'text': '🔎 Solscan', 'url': f'https://solscan.io/token/{mint}'},
        ],
    ]
    social = [
        {'text': '𝕏 X', 'url': signal.twitter} if signal.twitter else None,
        {'text': '💬 Telegram', 'url': signal.telegram} if signal.telegram else None,
        {'text': '🌐 Website', 'url': signal.website} if signal.website else None,
    ]
    social = [item for item in social if item]
    if social:
        rows.append(social[:2])
        if len(social) > 2:
            rows.append(social[2:])
    rows.append([{'text': '📋 Salin CA', 'copy_text': {'text': mint}}])
    return json.dumps({'inline_keyboard': rows}, ensure_ascii=False)


def gambar(signal: Signal) -> str:
    return public_url(signal.banner) or public_url(signal.image)


class Telegram:
    def __init__(self, session: aiohttp.ClientSession, token: str, chat_id: str):
        self.session = session
        self.api = f'https://api.telegram.org/bot{token}'
        self.chat_id = chat_id
        self.pacing_lock = asyncio.Lock()
        self.last_send = 0.0

    async def _pacing(self):
        """Beri jeda antar pesan tanpa menunggu respons jaringan pesan lain."""
        async with self.pacing_lock:
            now = asyncio.get_running_loop().time()
            pause = max(0, .7 - (now - self.last_send))
            if pause:
                await asyncio.sleep(pause)
            self.last_send = asyncio.get_running_loop().time()

    async def _request(self, method: str, data: dict, timeout: float = 6):
        try:
            async with self.session.post(self.api + '/' + method, data=data,
                    timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                result = await response.json(content_type=None)
                if response.status == 200 and result.get('ok'):
                    return 'sent', result
                if response.status == 429:
                    delay = (result.get('parameters') or {}).get('retry_after', 1)
                    LOG.warning('Telegram dibatasi sementara: %ss', delay)
                    return 'limited', result
                LOG.debug('Telegram %s gagal: HTTP %s: %s', method,
                          response.status, str(result.get('description', ''))[:120])
                return 'failed', result
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            LOG.warning('Status pengiriman Telegram tidak pasti: %s', type(error).__name__)
            return 'uncertain', {}
        except (ValueError, TypeError):
            return 'failed', {}

    async def send(self, signal: Signal, sol_usd: float) -> tuple[str, int | None, str]:
        caption = pesan(signal, sol_usd)
        markup = tombol(signal)
        photo = gambar(signal)
        await self._pacing()
        if photo:
            state, result = await self._request('sendPhoto', {
                'chat_id': self.chat_id, 'photo': photo, 'caption': caption,
                'parse_mode': 'HTML', 'reply_markup': markup,
            }, timeout=2.5)
            if state == 'sent':
                return state, (result.get('result') or {}).get('message_id'), 'photo'
            if state == 'uncertain':
                return state, None, 'photo'
            # Gambar tidak boleh menggagalkan alert utama.
            if state == 'limited':
                await asyncio.sleep(min(5, max(1, int(
                    (result.get('parameters') or {}).get('retry_after', 1)))))
        state, result = await self._request('sendMessage', {
            'chat_id': self.chat_id, 'text': caption,
            'parse_mode': 'HTML', 'reply_markup': markup,
            'link_preview_options': json.dumps({'is_disabled': True}),
        }, timeout=8)
        return state, (result.get('result') or {}).get('message_id'), 'text'

    async def update(self, message_id: int, signal: Signal, sol_usd: float, *,
                     kind: str, previous: Signal) -> None:
        text = pesan(signal, sol_usd)
        markup = tombol(signal)
        photo = gambar(signal)
        old_photo = gambar(previous)
        if kind == 'photo' and photo and photo != old_photo:
            state, _ = await self._request('editMessageMedia', {
                'chat_id': self.chat_id, 'message_id': message_id,
                'media': json.dumps({'type': 'photo', 'media': photo,
                                     'caption': text, 'parse_mode': 'HTML'}),
                'reply_markup': markup,
            })
            if state == 'sent':
                return
        method = 'editMessageCaption' if kind == 'photo' else 'editMessageText'
        body = {
            'chat_id': self.chat_id, 'message_id': message_id,
            'parse_mode': 'HTML', 'reply_markup': markup,
        }
        body['caption' if kind == 'photo' else 'text'] = text
        await self._request(method, body)

        if kind == 'text' and photo and not old_photo:
            # Metadata kadang muncul setelah alert. Kirim gambar hanya satu kali,
            # sebagai balasan agar hubungannya dengan token tetap jelas.
            await self._request('sendPhoto', {
                'chat_id': self.chat_id, 'photo': photo,
                'caption': f'🖼️ Gambar ${html.escape(signal.symbol)}',
                'parse_mode': 'HTML',
                'reply_parameters': json.dumps({'message_id': message_id}),
            }, timeout=5)
