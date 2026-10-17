#!/usr/bin/env python3
"""Solter command line: run | doctor | stats | test | report | probe."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time

from solter import __version__
from solter.config import Config
from solter.storage import Store


def main():
    parser = argparse.ArgumentParser(description=f'Solter v{__version__}: free early memecoin caller')
    parser.add_argument('command', choices=('run', 'doctor', 'stats', 'test', 'report', 'probe'))
    parser.add_argument('--days', type=int, default=7, help='report: days to include')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    cfg = Config.load()
    if args.command == 'doctor':
        print(json.dumps({'version': __version__, **cfg.public(), 'paid_feeds': False,
                          'trade_execution': False}, indent=2, default=str))
        return
    store = Store(cfg.db_path)
    try:
        if args.command == 'stats':
            print(json.dumps(store.stats(), indent=2))
        elif args.command == 'report':
            from solter.report import build, render
            print(render(build(store, time.time(), max(1, args.days))))
        elif args.command == 'probe':
            from solter.engine import probe
            print(json.dumps(asyncio.run(probe(cfg, store)), indent=2, default=str))
        elif args.command == 'test':
            if not cfg.tg_token or not cfg.tg_chat:
                parser.error('TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are missing in .env')
            print('Telegram:', asyncio.run(_telegram_test(cfg)))
        else:
            from solter.engine import Solter
            try:
                asyncio.run(Solter(cfg, store).run())
            except KeyboardInterrupt:
                print('\nSolter stopped')
    finally:
        store.close()


async def _telegram_test(cfg: Config) -> str:
    import aiohttp
    from solter.telegram import Telegram
    async with aiohttp.ClientSession() as session:
        state, _ = await Telegram(cfg.tg_token, cfg.tg_chat, session).send(
            'sendMessage', {'chat_id': cfg.tg_chat, 'text': f'✅ Solter v{__version__} Telegram terhubung'})
        return state


if __name__ == '__main__':
    main()
