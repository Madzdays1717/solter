"""Only classify verified net SPL token changes of a signer, never raw log counts.

This is NOT a reliable notional size in SOL/USD, funding-link detector, or
proof of organically independent wallets. One ambiguous tx == no trade.
"""
from __future__ import annotations
import time
from collections import defaultdict,Counter
from dataclasses import dataclass
from .chain import PUMP_PROGRAM

@dataclass(frozen=True)
class Trade:
    wallet:str
    side:str
    amount:float
    at:float

def _amount(entry:dict)->int:
    try:return int(entry['uiTokenAmount']['amount'])
    except (ValueError,KeyError,TypeError):return 0

def parse_trade(tx:dict|None,mint:str,curve:str,program:str=PUMP_PROGRAM)->Trade|None:
    if not isinstance(tx,dict):return None
    meta=tx.get('meta') or {}
    if meta.get('err') is not None:return None
    logs=meta.get('logMessages') or []
    if not any(f'Program {program} invoke' in str(x) for x in logs):return None
    message=((tx.get('transaction') or {}).get('message') or {})
    keys=message.get('accountKeys') or []
    signers={k.get('pubkey') for k in keys if isinstance(k,dict) and k.get('signer') is True}
    if not signers:return None
    def owners(rows):
        result=defaultdict(int)
        for r in rows or []:
            if r.get('mint')!=mint:continue
            owner=r.get('owner')
            if not owner:continue
            result[owner]+=_amount(r)
        return result
    before=owners(meta.get('preTokenBalances'));after=owners(meta.get('postTokenBalances'))
    if not before and not after:return None
    deltas={w:after.get(w,0)-before.get(w,0) for w in set(before)|set(after)}
    matched=[(w,d) for w,d in deltas.items() if w in signers and w!=curve and d!=0]
    # Multisigner and multi-trade transactions require more sophisticated decoding.
    if len(matched)!=1:return None
    w,delta=matched[0]
    at=tx.get('blockTime')
    if not isinstance(at,(int,float)) or at<=0 or abs(time.time()-at)>600:return None
    return Trade(w,'buy' if delta>0 else 'sell',abs(delta),float(at))

@dataclass(frozen=True)
class Signal:
    ok:bool
    reason:str
    buys:int=0
    sells:int=0
    buyers:int=0
    recent_buyers:int=0
    burst:int=0

def evaluate(trades:list[tuple],now:float|None=None,min_buys:int=6,min_unique:int=5)->Signal:
    now=now or time.time()
    valid=[(at,w,side) for at,w,side,_ in trades if 0<=now-at<=150 and side in ('buy','sell')]
    buys=[(at,w) for at,w,side in valid if side=='buy']
    sells=[(at,w) for at,w,side in valid if side=='sell']
    unique={w for _,w in buys}
    recent={(w) for at,w in buys if now-at<=45}
    burst=max((sum(1 for tb,_ in buys if 0<=tb-at<=20) for at,_ in buys),default=0)
    if len(buys)<min_buys:return Signal(False,'insufficient buys',len(buys),len(sells),len(unique),len(recent),burst)
    if len(unique)<min_unique:return Signal(False,'insufficient independent addresses observed',len(buys),len(sells),len(unique),len(recent),burst)
    if len(recent)<3 or burst<3:return Signal(False,'buy momentum not current',len(buys),len(sells),len(unique),len(recent),burst)
    if len(buys)<len(sells)*1.8:return Signal(False,'sell transactions dominate',len(buys),len(sells),len(unique),len(recent),burst)
    counts=Counter(w for _,w in buys)
    if max(counts.values())/len(buys)>.35:return Signal(False,'one wallet dominates buy count',len(buys),len(sells),len(unique),len(recent),burst)
    if max(at for at,_ in buys)-min(at for at,_ in buys)<15:
        return Signal(False,'insufficient observation window',len(buys),len(sells),len(unique),len(recent),burst)
    return Signal(True,'confirmed sequential buy activity',len(buys),len(sells),len(unique),len(recent),burst)
