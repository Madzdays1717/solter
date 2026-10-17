#!/usr/bin/env python3
"""Apply v2.0.2 watch settings without changing keys, filters or database."""
from pathlib import Path
import re
import shutil
import time

root = Path(__file__).resolve().parent
path = root / '.env'
if not path.is_file():
    raise SystemExit('Buat .env dari .env.example dan isi Telegram terlebih dahulu.')
original = path.read_text()
backup = root / ('.env.backup-' + str(time.time_ns()))
shutil.copy2(path, backup)
text = original
for key, value in {'MAX_ACTIVE_TOKENS': '16', 'EVAL_CONCURRENCY': '3', 'QUIET_SECONDS': '60'}.items():
    pattern = rf'^\s*(?:export\s+)?{key}\s*=.*$'
    line = f'{key}={value}'
    if re.search(pattern, text, re.M):
        text = re.sub(pattern, line, text, flags=re.M)
    else:
        text = text.rstrip() + '\n' + line + '\n'
path.write_text(text)
print('Watch=16, workers=3, quiet=60s. Backup .env dibuat. Filter dan kredensial dipertahankan.')
