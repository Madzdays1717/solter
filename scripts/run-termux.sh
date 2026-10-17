#!/data/data/com.termux/files/usr/bin/bash
set -e
cd "$(dirname "$0")/.."
exec python bot.py run
