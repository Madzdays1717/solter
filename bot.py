#!/usr/bin/env python3
"""Solter command-line entry point."""

import argparse
import asyncio
import json
import logging

from solter import __version__
from solter.addresses import valid_address
from solter.app import App
from solter.config import Config
from solter.storage import Storage


def main():
    parser = argparse.ArgumentParser(description=f'Solter {__version__}')
    parser.add_argument('command', choices=('run', 'doctor', 'stats', 'trace'))
    parser.add_argument('--mint', help='Solana token address')
    args = parser.parse_args()
    config = Config.load()
    if args.command == 'doctor':
        print(json.dumps({
            'version': __version__,
            'telegram_configured': config.telegram_ready,
            'market': 'Pump.fun / PumpSwap',
            'feed': 'live transactions',
            'social': 'public posts (best effort)',
            'database': str(config.database_path),
        }, indent=2))
        return
    if args.command == 'run' and not config.telegram_ready:
        parser.error('Isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID dalam .env')
    if args.command == 'trace' and not valid_address(args.mint):
        parser.error('Gunakan --mint dengan alamat token Solana')
    storage = Storage(config.database_path)
    try:
        if args.command == 'stats':
            print(json.dumps(storage.stats(), indent=2))
        elif args.command == 'trace':
            print(json.dumps(storage.trace(args.mint), indent=2))
        else:
            logging.basicConfig(level=logging.INFO,
                format='%(asctime)s %(levelname)-7s %(name)s: %(message)s')
            try:
                asyncio.run(App(config, storage).run())
            except KeyboardInterrupt:
                print('Solter stopped')
    finally:
        storage.close()


if __name__ == '__main__':
    main()
