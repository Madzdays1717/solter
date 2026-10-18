#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
exec python bot.py run
