"""Socket.IO connection for Pump.fun and PumpSwap transaction events."""

import asyncio
import json
import logging

import aiohttp

LOG = logging.getLogger('solter.stream')
HOST = 'wss://sol.shrine.trade/socket.io/?EIO=4&transport=websocket'
SUBSCRIPTION = {
    'protocols': ['PUMPFUN', 'PUMPFUN_MAYHEM', 'PUMPSWAP'],
    'actions': ['create', 'createPool', 'migrate', 'buy', 'sell'],
}


class TradeStream:
    def __init__(self, on_event, checkpoint):
        self.on_event = on_event
        self.checkpoint = checkpoint
        self.connected = False
        self.events_seen = 0

    async def run(self, session: aiohttp.ClientSession):
        backoff = 2
        while True:
            try:
                async with session.ws_connect(HOST, timeout=12, max_msg_size=2**20) as ws:
                    self.connected = False
                    await self._consume(ws)
                    backoff = 2
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                LOG.warning('Stream connection interrupted: %s', type(exc).__name__)
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(45, backoff * 2)

    async def _consume(self, ws: aiohttp.ClientWebSocketResponse):
        subscribed = False
        async for message in ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                if message.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    break
                continue
            frame = message.data
            if frame.startswith('0'):
                await ws.send_str('40')
            elif frame == '2':
                await ws.send_str('3')
            elif frame.startswith('40') and not subscribed:
                payload = dict(SUBSCRIPTION)
                slot, saved_at = self.checkpoint()
                if slot and saved_at:
                    import time
                    if time.time() - saved_at < 300:
                        payload['sinceSlot'] = slot
                await ws.send_str('42' + json.dumps(['subscribe_stream', payload], separators=(',', ':')))
                await ws.send_str('42["subscribe_sol_price"]')
                subscribed = True
                self.connected = True
                LOG.info('Pump.fun / PumpSwap transaction stream subscribed')
            elif frame.startswith('42'):
                try:
                    packet = json.loads(frame[2:])
                    if isinstance(packet, list) and len(packet) >= 2:
                        if packet[0] in ('stream', 'sol_price'):
                            self.events_seen += packet[0] == 'stream'
                            self.on_event(packet[0], packet[1])
                except (ValueError, TypeError):
                    LOG.warning('Unexpected event payload')
            elif frame.startswith('44'):
                LOG.error('Stream subscription rejected: %s', frame[2:250])
                break
