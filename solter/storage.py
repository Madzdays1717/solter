"""SQLite transactional state for process restart and call deduplication."""
from __future__ import annotations
import sqlite3
import time
from pathlib import Path

class Store:
    def __init__(self,path:Path):
        path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,timeout=15)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=10000')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS tokens (
          mint TEXT PRIMARY KEY, symbol TEXT, name TEXT, first_seen REAL,
          stage TEXT NOT NULL DEFAULT 'new', state TEXT NOT NULL DEFAULT 'queued',
          curve TEXT, first_price REAL, last_price REAL, last_price_at REAL,
          image TEXT, note TEXT NOT NULL DEFAULT '', expires REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS tokens_active ON tokens(state,expires);
        CREATE TABLE IF NOT EXISTS trades (
          signature TEXT NOT NULL, mint TEXT NOT NULL, at REAL NOT NULL,
          wallet TEXT NOT NULL, side TEXT NOT NULL, token_amount REAL NOT NULL,
          PRIMARY KEY(signature,mint));
        CREATE INDEX IF NOT EXISTS trades_by_mint ON trades(mint,at);
        CREATE TABLE IF NOT EXISTS calls (
          mint TEXT PRIMARY KEY, at REAL NOT NULL, state TEXT NOT NULL,
          stage TEXT NOT NULL, message TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS calls_at ON calls(at);
        CREATE TABLE IF NOT EXISTS processed (
          signature TEXT NOT NULL,mint TEXT NOT NULL,at REAL NOT NULL,
          PRIMARY KEY(signature,mint));
        CREATE INDEX IF NOT EXISTS processed_at ON processed(at);
        CREATE TABLE IF NOT EXISTS budget (
          day TEXT PRIMARY KEY, used INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS metrics (
          key TEXT PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0);
        ''')
        self.db.commit()

    def metric(self,key:str,value:int=1):
        self.db.execute('INSERT INTO metrics(key,n) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET n=n+excluded.n',(key,value));self.db.commit()

    def admit(self,mint:str,event:dict,capacity:int,watch_seconds:int)->bool:
        if self.db.execute('SELECT 1 FROM calls WHERE mint=?',(mint,)).fetchone():return False
        existing=self.db.execute('SELECT state FROM tokens WHERE mint=?',(mint,)).fetchone()
        if existing and existing[0]!='expired':return False
        count=self.db.execute('SELECT COUNT(*) FROM tokens WHERE state="watching" AND expires>?',(time.time(),)).fetchone()[0]
        if count>=capacity:self.metric('skipped_capacity');return False
        now=time.time()
        if existing:
            self.db.execute('DELETE FROM trades WHERE mint=?',(mint,))
            self.db.execute('''UPDATE tokens SET first_seen=?,stage='new',state='watching',
                curve='',first_price=NULL,last_price=NULL,last_price_at=NULL,
                expires=? WHERE mint=?''',(now,now+watch_seconds,mint))
        else:
            self.db.execute('''INSERT INTO tokens
              (mint,symbol,name,first_seen,stage,state,curve,expires)
              VALUES(?,?,?,?,?,?,?,?)''',(mint,str(event.get('symbol') or '')[:32],
                 str(event.get('name') or '')[:80],now,'new','watching','',now+watch_seconds))
        self.db.commit();self.metric('admitted');return True

    def expire(self):
        self.db.execute('UPDATE tokens SET state="expired" WHERE state="watching" AND expires<?',(time.time(),))
        self.db.execute('DELETE FROM trades WHERE at<?',(time.time()-3*86400,))
        self.db.execute('DELETE FROM processed WHERE at<?',(time.time()-86400,))
        self.db.execute('DELETE FROM tokens WHERE first_seen<? AND state="expired"',(time.time()-3*86400,))
        self.db.execute('DELETE FROM budget WHERE day<?',(time.strftime('%Y-%m-%d',time.gmtime(time.time()-7*86400)),))
        self.db.commit()

    def watching(self)->list[dict]:
        rows=self.db.execute('SELECT mint,curve,first_seen,stage,symbol,name,first_price,last_price,last_price_at,image FROM tokens WHERE state="watching" AND expires>? ORDER BY first_seen',(time.time(),)).fetchall()
        return [dict(zip(('mint','curve','first_seen','stage','symbol','name','first_price','last_price','last_price_at','image'),r)) for r in rows]

    def set_curve(self,mint:str,curve:str):
        self.db.execute('UPDATE tokens SET curve=? WHERE mint=?',(curve,mint));self.db.commit()

    def migrate(self,mint:str):
        # New AMM market: watch pool, clear the old curve and prior trade flow.
        changed=self.db.execute("""UPDATE tokens SET stage='migrated',curve='',
            first_price=NULL,last_price=NULL,last_price_at=NULL
            WHERE mint=? AND stage!='migrated'""",(mint,)).rowcount
        if changed:self.db.execute('DELETE FROM trades WHERE mint=?',(mint,))
        self.db.commit()

    def record_trade(self,mint:str,sig:str,wallet:str,side:str,amount:float,at:float)->bool:
        if not self.db.execute('SELECT 1 FROM tokens WHERE mint=? AND state="watching"',(mint,)).fetchone():return False
        cur=self.db.execute('INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?,?)',(sig,mint,at,wallet,side,amount))
        self.db.commit();return cur.rowcount>0

    def seen_trade(self,mint:str,sig:str)->bool:
        return bool(self.db.execute('SELECT 1 FROM processed WHERE mint=? AND signature=?',(mint,sig)).fetchone())

    def mark_processed(self,mint:str,sig:str):
        self.db.execute('INSERT OR IGNORE INTO processed VALUES(?,?,?)',(sig,mint,time.time()))
        self.db.commit()

    def trades(self,mint:str,seconds:int=180)->list[tuple]:
        return self.db.execute('SELECT at,wallet,side,token_amount FROM trades WHERE mint=? AND at>=? ORDER BY at',(mint,time.time()-seconds)).fetchall()

    def set_market(self,mint:str,price:float|None,img:str|None):
        if price is not None and price>0:
            self.db.execute('''UPDATE tokens SET first_price=COALESCE(first_price,?),
              last_price=?,last_price_at=? WHERE mint=?''',(price,price,time.time(),mint))
        if img and img.startswith('https://'):
            self.db.execute('UPDATE tokens SET image=? WHERE mint=?',(img[:1600],mint))
        self.db.commit()

    def budget_claim(self,limit:int)->bool:
        day=time.strftime('%Y-%m-%d',time.gmtime())
        self.db.execute('INSERT OR IGNORE INTO budget(day,used) VALUES(?,0)',(day,))
        n=self.db.execute('SELECT used FROM budget WHERE day=?',(day,)).fetchone()[0]
        if n>=limit:return False
        self.db.execute('UPDATE budget SET used=used+1 WHERE day=?',(day,))
        self.db.commit();return True

    def budget_used(self)->int:
        day=time.strftime('%Y-%m-%d',time.gmtime())
        row=self.db.execute('SELECT used FROM budget WHERE day=?',(day,)).fetchone()
        return row[0] if row else 0

    def has_call(self,mint:str)->bool:
        return bool(self.db.execute('SELECT 1 FROM calls WHERE mint=?',(mint,)).fetchone())

    def claim_call(self,mint:str,stage:str,cap:int)->bool:
        if self.db.execute('SELECT COUNT(*) FROM calls WHERE at>?',(time.time()-3600,)).fetchone()[0]>=cap:
            return False
        cur=self.db.execute('INSERT OR IGNORE INTO calls(mint,at,state,stage) VALUES(?,?,?,?)',(mint,time.time(),'attempting',stage))
        self.db.commit();return cur.rowcount==1

    def finish_call(self,mint:str,state:str,message:str=''):
        self.db.execute('UPDATE calls SET state=?,message=? WHERE mint=?',(state,message[:200],mint))
        self.db.execute('UPDATE tokens SET state="called" WHERE mint=?',(mint,))
        self.db.commit()

    def stats(self)->dict:
        return {'tokens':self.db.execute('SELECT state,COUNT(*) FROM tokens GROUP BY state').fetchall(),
                'calls':self.db.execute('SELECT state,COUNT(*) FROM calls GROUP BY state').fetchall(),
                'metrics':self.db.execute('SELECT key,n FROM metrics ORDER BY key').fetchall(),
                'rpc_today':self.budget_used()}
