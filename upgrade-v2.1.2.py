"""Explicitly upgrade performance settings, preserving all filter/user credentials."""
from pathlib import Path
import re
import shutil
import time
root=Path(__file__).resolve().parent
p=root/'.env'
if not p.is_file():
    raise SystemExit('Salin .env.example ke .env lalu isi Telegram.')
shutil.copy2(p,root/('.env.backup-'+str(time.time_ns())))
s=p.read_text()
settings={'MAX_ACTIVE_TOKENS':'50','EVAL_CONCURRENCY':'3','RADAR_CONCURRENCY':'2',
          'RPC_REQUESTS_PER_MIN':'300','RPC_REQUESTS_PER_DAY':'200000',
          'RPC_REQUESTS_PER_SECOND':'5','RPC_METHOD_REQUESTS_PER_SECOND':'2',
          'RADAR_MAX_SAMPLE_AGE_SECONDS':'45','RADAR_CONFIRM_PRICE_RISE_PCT':'1',
          'RADAR_MAX_LIQUIDITY_DROP_PCT':'10'}
for k,v in settings.items():
    pattern=rf'^\s*(?:export\s+)?{k}\s*=.*$'
    s=re.sub(pattern,k+'='+v,s,flags=re.M) if re.search(pattern,s,re.M) else s.rstrip()+'\n'+k+'='+v+'\n'
p.write_text(s)
print('Pengaturan v2.1.2 diterapkan; filter kualitas dan kredensial tetap. Backup .env dibuat.')
