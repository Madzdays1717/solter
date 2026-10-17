"""Solter v1 configuration. No billable API methods or trading keys."""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def env_bool(k: str, default=False) -> bool:
    return os.getenv(k, str(default)).strip().lower() in {'yes','true','1','on'}

@dataclass(frozen=True)
class Config:
    tg_token: str
    tg_chat: str
    rpc_http: str
    rpc_ws: str
    dex_url: str
    max_active: int
    watch_seconds: int
    rpc_per_minute: int
    rpc_per_day: int
    dex_interval: float
    db_path: Path
    max_calls_per_hour: int
    min_distinct_buyers: int
    min_buys: int
    max_lag_seconds: int
    require_mint_check: bool

    @classmethod
    def load(cls) -> 'Config':
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env')
        http=os.getenv('SOLANA_RPC_URL','https://api.mainnet-beta.solana.com').strip()
        ws=os.getenv('SOLANA_RPC_WS_URL','').strip() or http.replace('https://','wss://').replace('http://','ws://')
        c=cls(
            os.getenv('TELEGRAM_BOT_TOKEN','').strip(),os.getenv('TELEGRAM_CHAT_ID','').strip(),
            http,ws,'https://api.dexscreener.com',
            int(os.getenv('MAX_ACTIVE_TOKENS','5')),
            int(os.getenv('WATCH_SECONDS','240')),
            int(os.getenv('RPC_REQUESTS_PER_MIN','30')),
            int(os.getenv('RPC_REQUESTS_PER_DAY','1800')),
            float(os.getenv('DEX_SECONDS_BETWEEN','4')),
            ROOT/'data'/'solter.sqlite3',
            int(os.getenv('MAX_CALLS_PER_HOUR','5')),
            int(os.getenv('MIN_UNIQUE_BUYERS','5')),
            int(os.getenv('MIN_BUYS','6')),
            int(os.getenv('MAX_EVENT_LAG_SECONDS','35')),
            env_bool('REQUIRE_MINT_CHECK',True)
        )
        if not (c.rpc_http.startswith('https://') and c.rpc_ws.startswith('wss://')):
            raise ValueError('RPC endpoints must use HTTPS and WSS')
        if not (1<=c.max_active<=20 and 30<=c.watch_seconds<=1800):
            raise ValueError('invalid active capacity / watch duration')
        if not (1<=c.rpc_per_minute<=120 and 10<=c.rpc_per_day<=20000 and c.dex_interval>=1):
            raise ValueError('invalid free-tier budget')
        if not (1<=c.min_distinct_buyers<=30 and 1<=c.min_buys<=100 and 5<=c.max_lag_seconds<=300):
            raise ValueError('invalid signal thresholds')
        return c
