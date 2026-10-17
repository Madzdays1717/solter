"""Solter v1: free-tier bounded, event-driven opportunity scanner.

Trade inference is deliberately conservative: ambiguous transactions are ignored.
No wallet private keys, paid trade API calls, or automatic execution.
"""
from __future__ import annotations
import asyncio
import html
import json
import logging
import random
import time
from collections import deque
from urllib.parse import quote

import aiohttp
from .chain import PUMP_PROGRAM, bonding_pda, mint_safety, curve_metadata, valid_address
from .config import Config
from .flow import evaluate,parse_trade
from .storage import Store

LOG=logging.getLogger('solter')
PUMP_WS='wss://pumpportal.fun/api/data'
PUMP_AMM='pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA'

class NoBudget(RuntimeError): pass

class BoundedRPC:
    def __init__(self,cfg:Config,db:Store,session:aiohttp.ClientSession):
        self.cfg=cfg;self.db=db;self.session=session;self.recent=deque();self.lock=asyncio.Lock()
        self.failures=0

    async def _take_budget(self):
        async with self.lock:
            now=time.monotonic()
            while self.recent and now-self.recent[0]>=60:self.recent.popleft()
            if len(self.recent)>=self.cfg.rpc_per_minute:
                await asyncio.sleep(max(.05,60-(now-self.recent[0])))
            now=time.monotonic()
            while self.recent and now-self.recent[0]>=60:self.recent.popleft()
            if not self.db.budget_claim(self.cfg.rpc_per_day):raise NoBudget('daily RPC budget reached')
            self.recent.append(time.monotonic())

    async def call(self,method:str,params:list)->object:
        for attempt in range(2):
            # Every actual HTTP attempt consumes one request from the local budget.
            await self._take_budget()
            try:
                async with self.session.post(self.cfg.rpc_http,json={
                    'jsonrpc':'2.0','id':1,'method':method,'params':params},
                    timeout=aiohttp.ClientTimeout(total=12)) as resp:
                    if resp.status==429:
                        self.failures+=1
                        if attempt==0:await asyncio.sleep(3);continue
                        return None
                    if resp.status>=400:
                        LOG.warning('RPC HTTP %s method %s',resp.status,method);return None
                    obj=await resp.json(content_type=None)
                    if obj.get('error'):
                        LOG.warning('RPC %s error %s',method,str(obj['error'])[:140]);return None
                    self.failures=0
                    return obj.get('result')
            except (aiohttp.ClientError,asyncio.TimeoutError,ValueError,TypeError) as e:
                self.failures+=1;LOG.warning('RPC %s failed: %s',method,type(e).__name__)
                if attempt==0:await asyncio.sleep(1)
        return None

class Solter:
    def __init__(self,cfg:Config,db:Store):
        self.cfg=cfg;self.db=db;self.session=None;self.rpc=None
        self.queue=asyncio.Queue(maxsize=400)
        self.running=True;self.dex_lock=asyncio.Lock();self.last_dex=0.
        self.mint_verdict={};self.curve_verdict={};self.seen_signatures=set();self.last_live={}
        self.rpc_ws_connected=False;self.recover_until=0.;self.backfilled_once=set()

    async def discover(self):
        backoff=2
        while self.running:
            try:
                async with self.session.ws_connect(PUMP_WS,heartbeat=25,timeout=20,max_msg_size=2**18) as ws:
                    LOG.info('PumpPortal connected (free discovery streams)')
                    self.db.metric('pump_connect')
                    await ws.send_json({'method':'subscribeNewToken'})
                    await ws.send_json({'method':'subscribeMigration'})
                    backoff=2
                    async for msg in ws:
                        if msg.type!=aiohttp.WSMsgType.TEXT:break
                        try:e=json.loads(msg.data)
                        except (ValueError,TypeError):continue
                        if not isinstance(e,dict):continue
                        mint=e.get('mint')
                        if not valid_address(mint):continue
                        kind=str(e.get('txType','')).lower()
                        if kind in ('migrate','migration') or e.get('pool') and kind!='create':
                            self.db.migrate(mint);self.db.metric('migration_seen')
                            # A mint remains a single opportunity; migration is a stage, not a second call.
                            if not self.db.has_call(mint) and not any(t['mint']==mint for t in self.db.watching()):
                                if self.db.admit(mint,e,self.cfg.max_active,self.cfg.watch_seconds):
                                    self.db.migrate(mint)
                            continue
                        if kind=='create' or ('name' in e and 'symbol' in e):
                            self.db.metric('new_seen')
                            if self.db.admit(mint,e,self.cfg.max_active,self.cfg.watch_seconds):
                                LOG.info('DISCOVER %s %s',mint[:12],str(e.get('symbol',''))[:20])
            except (aiohttp.ClientError,asyncio.TimeoutError,ValueError) as exc:
                LOG.warning('PumpPortal disconnected: %s',type(exc).__name__)
            await asyncio.sleep(backoff+random.random())
            backoff=min(backoff*2,90)

    async def subscribe_rpc(self):
        """Single shared logs WebSocket, dynamic mention subscriptions for active candidates."""
        backoff=2
        while self.running:
            try:
                async with self.session.ws_connect(self.cfg.rpc_ws,heartbeat=25,timeout=20,max_msg_size=2**20) as ws:
                    LOG.info('Solana logs WebSocket connected')
                    self.rpc_ws_connected=True;self.recover_until=time.time()+40
                    backoff=2;req=0;active={};pending={};subscription={}
                    while self.running:
                        target={t['mint']:t['curve'] for t in self.db.watching() if t['curve']}
                        for mint in list(active):
                            if mint not in target or active[mint][0]!=target[mint]:
                                sub=active.pop(mint)[1]
                                if sub is not None:
                                    req+=1;await ws.send_json({'jsonrpc':'2.0','id':req,'method':'logsUnsubscribe','params':[sub]})
                                    subscription.pop(sub,None)
                        for mint,account in target.items():
                            if mint in active or any(p[0]==mint for p in pending.values()):continue
                            req+=1;pending[req]=(mint,account)
                            await ws.send_json({'jsonrpc':'2.0','id':req,'method':'logsSubscribe',
                                  'params':[{'mentions':[account]},{'commitment':'confirmed'}]})
                        try:msg=await asyncio.wait_for(ws.receive(),timeout=3)
                        except asyncio.TimeoutError:continue
                        if msg.type!=aiohttp.WSMsgType.TEXT:break
                        try:j=json.loads(msg.data)
                        except ValueError:continue
                        if not isinstance(j,dict):continue
                        if 'id' in j and j['id'] in pending:
                            mint,account=pending.pop(j['id'])
                            sub=j.get('result')
                            if isinstance(sub,int):
                                active[mint]=(account,sub);subscription[sub]=mint
                            else:LOG.warning('Subscription refused for %s',mint[:10]);self.db.metric('ws_sub_error')
                        if j.get('method')=='logsNotification':
                            p=j.get('params') or {};v=(p.get('result') or {}).get('value') or {}
                            sub=p.get('subscription');mint=subscription.get(sub)
                            sig=v.get('signature')
                            if mint and isinstance(sig,str) and v.get('err') is None:
                                self.last_live[mint]=time.time()
                                self.enqueue(mint,sig)
            except (aiohttp.ClientError,asyncio.TimeoutError,ValueError) as e:
                LOG.warning('Solana WebSocket reconnect: %s',type(e).__name__)
            finally:
                self.rpc_ws_connected=False
            await asyncio.sleep(backoff+random.random());backoff=min(backoff*2,90)

    def enqueue(self,mint:str,sig:str):
        if self.db.seen_trade(mint,sig) or (mint,sig) in self.seen_signatures:return
        if self.queue.full():self.db.metric('signature_queue_drop');return
        self.seen_signatures.add((mint,sig))
        self.queue.put_nowait((mint,sig))

    async def derive_watch_accounts(self):
        while self.running:
            for t in self.db.watching():
                if t['curve']:continue
                try:
                    if t['stage']=='migrated':
                        pair=await self.fetch_pair(t['mint'],'migrated')
                        address=pair.get('pairAddress') if pair else None
                        if not valid_address(address):continue
                    else:
                        address=bonding_pda(t['mint'])
                        pair=await self.fetch_pair(t['mint'],'new')
                    self.db.set_curve(t['mint'],address)
                    price,img=self.extract_price(pair)
                    if price:self.db.set_market(t['mint'],price,img)
                except ValueError:LOG.warning('Could not derive watch account: %s',t['mint'])
            await asyncio.sleep(5)

    async def backfill(self):
        """Limited best-effort catch-up after WS disconnect, no paid data source."""
        while self.running:
            try:
                for t in self.db.watching():
                    if not t['curve']:continue
                    mint=t['mint']
                    # Backfill at first observation and after a WebSocket outage.
                    # Avoid burning the free API budget when the stream is healthy.
                    needed=(mint not in self.backfilled_once or
                            not self.rpc_ws_connected or time.time()<self.recover_until)
                    if not needed:continue
                    try:
                        items=await self.rpc.call('getSignaturesForAddress',
                            [t['curve'],{'limit':12,'commitment':'confirmed'}])
                    except NoBudget:
                        LOG.warning('RPC daily cap reached. Continuing discovery; pause deep analysis.')
                        break
                    if not isinstance(items,list):continue
                    self.backfilled_once.add(mint)
                    for item in reversed(items):
                        sig=item.get('signature')
                        if item.get('err') is None and isinstance(sig,str):self.enqueue(t['mint'],sig)
            except Exception:LOG.exception('Backfill cycle error')
            await asyncio.sleep(22)

    async def transactions(self):
        while self.running:
            mint,sig=await self.queue.get()
            try:
                token=next((t for t in self.db.watching() if t['mint']==mint),None)
                if not token or not token['curve']:continue
                try:tx=await self.rpc.call('getTransaction',[sig,{
                    'encoding':'jsonParsed','commitment':'confirmed','maxSupportedTransactionVersion':1}])
                except NoBudget:
                    await asyncio.sleep(30);continue
                if tx is None:continue
                # V1 limits to exact supported Pump.fun / PumpSwap invocations.
                program=PUMP_AMM if token['stage']=='migrated' else PUMP_PROGRAM
                trade=parse_trade(tx,mint,token['curve'],program=program)
                self.db.mark_processed(mint,sig)
                if trade and time.time()-trade.at<=self.cfg.max_lag_seconds:
                    if self.db.record_trade(mint,sig,trade.wallet,trade.side,trade.amount,trade.at):
                        self.db.metric('classified_trade')
            except Exception:LOG.exception('transaction parse error')
            finally:
                self.seen_signatures.discard((mint,sig));self.queue.task_done()

    async def fetch_pair(self,mint:str,stage:str)->dict|None:
        async with self.dex_lock:
            await asyncio.sleep(max(0.,self.cfg.dex_interval-(time.monotonic()-self.last_dex)))
            self.last_dex=time.monotonic()
        try:
            async with self.session.get(f'https://api.dexscreener.com/token-pairs/v1/solana/{quote(mint,safe="")}',
                                        timeout=aiohttp.ClientTimeout(total=12)) as response:
                if response.status!=200:return None
                raw=await response.json(content_type=None)
                if not isinstance(raw,list):return None
                allowed=('pumpfun','pump') if stage=='new' else ('pumpswap','pumpamm')
                pairs=[p for p in raw if isinstance(p,dict)
                   and p.get('chainId')=='solana' and (p.get('baseToken') or {}).get('address')==mint
                   and str(p.get('dexId','')).lower().replace('-','') in allowed]
                return max(pairs,key=lambda p:float((p.get('liquidity') or {}).get('usd') or 0),default=None)
        except (aiohttp.ClientError,asyncio.TimeoutError,ValueError,TypeError):return None

    async def verified_mint(self,mint:str)->bool:
        entry=self.mint_verdict.get(mint)
        if entry and time.time()-entry[0]<80:return entry[1]
        try:result=await self.rpc.call('getAccountInfo',[mint,{'encoding':'jsonParsed','commitment':'confirmed'}])
        except NoBudget:return False
        value=result.get('value') if isinstance(result,dict) else None
        ok,note=mint_safety(value)
        self.mint_verdict[mint]=(time.time(),ok)
        if not ok:LOG.info('MINT %s %s',mint[:10],note)
        return ok

    async def verified_curve(self,mint:str,curve:str,trades:list[tuple])->bool:
        entry=self.curve_verdict.get(mint)
        if not entry or time.time()-entry[0]>60:
            try:result=await self.rpc.call('getAccountInfo',[curve,{'encoding':'base64','commitment':'confirmed'}])
            except NoBudget:return False
            value=result.get('value') if isinstance(result,dict) else None
            meta=curve_metadata(value)
            self.curve_verdict[mint]=(time.time(),meta)
        else:meta=entry[1]
        if not meta or meta['complete'] or meta['mayhem']:return False
        # If the creator sells within observed candidate flow, the token needs
        # manual scrutiny; fail closed for this simplified prototype.
        creator=meta['creator']
        return not any(w==creator and side=='sell' for _,w,side,_ in trades)

    @staticmethod
    def extract_price(pair:dict|None)->tuple[float|None,str|None]:
        if not pair:return None,None
        try:
            p=float(pair.get('priceUsd'))
            if not (0<p<1e10):p=None
        except (TypeError,ValueError,OverflowError):p=None
        info=pair.get('info') or {}
        image=info.get('imageUrl')
        if not isinstance(image,str) or not image.startswith('https://'):image=None
        return p,image

    async def market_sampler(self):
        """Baseline price is captured before a momentum signal, not at call time."""
        while self.running:
            try:
                for t in self.db.watching():
                    if self.db.has_call(t['mint']):continue
                    pair=await self.fetch_pair(t['mint'],t['stage'])
                    price,img=self.extract_price(pair)
                    if price:self.db.set_market(t['mint'],price,img)
            except Exception:LOG.exception('market sampling error')
            await asyncio.sleep(35)

    async def evaluate_candidates(self):
        while self.running:
            try:
                for t in self.db.watching():
                    if self.db.has_call(t['mint']):continue
                    trades=self.db.trades(t['mint'],150)
                    signal=evaluate(trades,min_buys=self.cfg.min_buys,
                                    min_unique=self.cfg.min_distinct_buyers)
                    if not signal.ok:continue
                    # Live price is mandatory, so early opportunities without indexed market
                    # data are deliberately excluded rather than inventing an entry price.
                    pair=await self.fetch_pair(t['mint'],t['stage'])
                    price,img=self.extract_price(pair)
                    if price is None:continue
                    self.db.set_market(t['mint'],price,img)
                    # Token price movement since first *verified* price snapshot.
                    token=next((x for x in self.db.watching() if x['mint']==t['mint']),None)
                    if not token:continue
                    baseline=token['first_price'] or price
                    change=(price/baseline-1)*100
                    if change>45 or change< -30:continue # Avoid chasing and breakdowns
                    if token['stage']=='migrated':
                        try:liq=float((pair.get('liquidity') or {}).get('usd') or 0)
                        except (ValueError,TypeError):liq=0
                        if liq<=0:continue
                    if self.cfg.require_mint_check and not await self.verified_mint(t['mint']):continue
                    if t['stage']=='new' and not await self.verified_curve(t['mint'],t['curve'],trades):continue
                    await self.send_call(token,signal,price,img,change)
            except Exception:LOG.exception('evaluation cycle failed')
            await asyncio.sleep(8)

    def call_text(self,t:dict,signal,price:float,change:float)->str:
        sym=html.escape(t['symbol'] or 'TOKEN');name=html.escape(t['name'] or '')
        age=int((time.time()-t['first_seen'])/60)
        stage='BONDING CURVE' if t['stage']=='new' else 'PUMPSWAP'
        ca=html.escape(t['mint'])
        return (f'⚡ <b>SOLTER | EARLY FLOW</b>\n'
                f'<b>{name} ({sym})</b>\n'
                f'\n<b>{stage}</b> · {age}m sejak terdeteksi\n'
                f'💰 Harga: <code>${price:.10g}</code>\n'
                f'📈 Sejak observasi harga: {change:+.1f}%\n'
                f'🟢 Buy {signal.buys} · 🔴 Sell {signal.sells}\n'
                f'👛 Buyer addresses {signal.buyers} · Baru aktif {signal.recent_buyers}\n'
                f'⚡ Buy burst {signal.burst}/20s\n'
                f'🛡️ Mint authority: revoked\n'
                f'\n<code>{ca}</code>')

    async def telegram(self,method:str,payload:dict)->str:
        """Return sent / rejected / uncertain; never auto-repeat uncertain requests."""
        try:
            async with self.session.post(f'https://api.telegram.org/bot{self.cfg.tg_token}/{method}',
                                         data=payload,timeout=aiohttp.ClientTimeout(total=15)) as r:
                data=await r.json(content_type=None)
                if r.status==200 and isinstance(data,dict) and data.get('ok'):return 'sent'
                if r.status==400:return 'rejected'
                return 'uncertain'
        except (aiohttp.ClientError,asyncio.TimeoutError,ValueError):return 'uncertain'

    async def send_call(self,t:dict,signal,price:float,img:str|None,change:float):
        if not self.cfg.tg_token or not self.cfg.tg_chat:return
        if not self.db.claim_call(t['mint'],t['stage'],self.cfg.max_calls_per_hour):return
        body=self.call_text(t,signal,price,change)
        mint=quote(t['mint'],safe='')
        keyboard=json.dumps({'inline_keyboard':[[
            {'text':'DexScreener','url':f'https://dexscreener.com/solana/{mint}'},
            {'text':'GMGN','url':f'https://gmgn.ai/sol/token/{mint}'},
            {'text':'FOMO','url':f'https://fomo.family/tokens/solana/{mint}'}
        ]]})
        common={'chat_id':self.cfg.tg_chat,'parse_mode':'HTML','reply_markup':keyboard}
        state='rejected'
        if img:
            state=await self.telegram('sendPhoto',{
                **common,'photo':img,'caption':body[:990]})
        if state=='rejected':
            state=await self.telegram('sendMessage',{**common,'text':body,'link_preview_options':json.dumps({'is_disabled':True})})
        self.db.finish_call(t['mint'],state,'single-message attempt')
        LOG.info('CALL %s %s delivery=%s',t['stage'],t['mint'][:12],state)

    async def maintenance(self):
        while self.running:
            self.db.expire()
            # Keep per-runtime memory bounded on noisy markets.
            self.mint_verdict={k:v for k,v in self.mint_verdict.items() if time.time()-v[0]<180}
            self.backfilled_once.intersection_update({t['mint'] for t in self.db.watching()})
            await asyncio.sleep(30)

    async def supervise(self,name:str,worker):
        """Restart any failed long-lived worker instead of terminating the bot."""
        backoff=3
        while self.running:
            try:
                await worker()
                if self.running:LOG.warning('worker %s unexpectedly returned',name)
            except asyncio.CancelledError:raise
            except Exception:
                LOG.exception('worker %s crashed, restarting',name)
                self.db.metric('worker_restart')
            if not self.running:break
            await asyncio.sleep(backoff)
            backoff=min(backoff*2,60)

    async def run(self):
        if not self.cfg.tg_token or not self.cfg.tg_chat:
            raise ValueError('TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID required')
        async with aiohttp.ClientSession(headers={'User-Agent':'Solter/1.0.0'}) as session:
            self.session=session;self.rpc=BoundedRPC(self.cfg,self.db,session)
            LOG.info('SOLTER v1 ready: active=%d RPC cap=%d/day (UTC) paid feeds=disabled',
                     self.cfg.max_active,self.cfg.rpc_per_day)
            workers=(self.discover,self.subscribe_rpc,self.derive_watch_accounts,
                     self.backfill,self.transactions,self.market_sampler,
                     self.evaluate_candidates,self.maintenance)
            jobs=[asyncio.create_task(self.supervise(f.__name__,f)) for f in workers]
            try:await asyncio.gather(*jobs)
            finally:
                self.running=False
                for j in jobs:j.cancel()
                await asyncio.gather(*jobs,return_exceptions=True)
