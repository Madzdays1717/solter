#!/usr/bin/env python3
"""Solter CLI: no modes, no legacy entry points, no hidden startup checks."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from solter import __version__
from solter.addresses import valid_mint
from solter.database import Database
from solter.scanner import Scanner
from solter.settings import Settings


def main():
    parser = argparse.ArgumentParser(description=f'Solter {__version__} market scanner')
    parser.add_argument('command', choices=('run', 'doctor', 'stats', 'trace'))
    parser.add_argument('--mint', help='Contract address, for trace')
    options = parser.parse_args()
    settings = Settings.load()
    if options.command == 'doctor':
        print(json.dumps({'version': __version__, **settings.diagnostic()}, indent=2))
        return
    if options.command == 'run' and not settings.telegram_ready:
        parser.error('Isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID di .env terlebih dahulu.')
    if options.command == 'trace' and not valid_mint(options.mint):
        parser.error('Gunakan trace --mint <alamat Solana yang valid>')
    database = Database(settings.database)
    try:
        if options.command == 'stats':
            print(json.dumps(database.stats(), indent=2))
        elif options.command == 'trace':
            print(json.dumps(database.trace(options.mint), indent=2))
        else:
            logging.basicConfig(level=logging.INFO,
                                format='%(asctime)s %(levelname)-7s %(name)s: %(message)s')
            try:
                asyncio.run(Scanner(settings, database).run())
            except KeyboardInterrupt:
                print('\nSolter dihentikan')
    finally:
        database.close()


if __name__ == '__main__':
    main()
