#!/usr/bin/env python3
from __future__ import annotations
import argparse
import asyncio
import json
import logging
from solter.config import Config
from solter.service import Solter
from solter.storage import Store

def main():
    p=argparse.ArgumentParser(description='Solter v1.0.0 early-flow watcher')
    p.add_argument('command',choices=('run','doctor','stats','test'))
    a=p.parse_args();logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    cfg=Config.load()
    if a.command=='doctor':
        print(json.dumps({'version':'1.0.0','telegram_configured':bool(cfg.tg_token and cfg.tg_chat),
              'rpc_http':cfg.rpc_http.split('?')[0], 'rpc_ws':cfg.rpc_ws.split('?')[0],
              'max_active':cfg.max_active,'rpc_requests_per_day':cfg.rpc_per_day,
              'rpc_requests_per_min':cfg.rpc_per_minute,'watch_seconds':cfg.watch_seconds,
              'trades_paid_feed':False,'trade_execution':False},indent=2));return
    db=Store(cfg.db_path)
    if a.command=='stats':print(json.dumps(db.stats(),indent=2));return
    if a.command=='test':
        if not cfg.tg_token or not cfg.tg_chat:p.error('Missing Telegram secrets in .env')
        async def do_test():
            import aiohttp
            async with aiohttp.ClientSession() as s:
                h=Solter(cfg,db);h.session=s
                return await h.telegram('sendMessage',{'chat_id':cfg.tg_chat,'text':'✅ Solter v1 Telegram connected'})
        print('Telegram:',asyncio.run(do_test()));return
    try:asyncio.run(Solter(cfg,db).run())
    except KeyboardInterrupt:print('\nSolter stopped')

if __name__=='__main__':main()
