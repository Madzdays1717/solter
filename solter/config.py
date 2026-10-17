"""Configuration from `.env`. Only free data sources; no trading keys exist here."""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path

from .flow import Rules
from .quality import QualityRules
from .reservoir import ReservoirRules

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    tg_token: str = ''
    tg_chat: str = ''
    rpc_http: str = 'https://api.mainnet-beta.solana.com'
    rpc_ws: str = 'wss://api.mainnet-beta.solana.com'
    db_path: Path = ROOT / 'data' / 'solter-v2.sqlite3'
    # Request ceilings this installation keeps by itself (not a provider's credit accounting).
    rpc_per_minute: int = 60
    rpc_per_day: int = 15000
    dex_interval: float = 1.0
    # Reservoir and live watch
    sweep_seconds: int = 30
    sweep_accounts: int = 200
    reservoir_minutes: int = 60
    alive_min_sol: float = 0.5
    promote_min_rate: float = 0.5
    promote_min_sol: float = 1.0
    max_watch: int = 16
    eval_concurrency: int = 3
    quiet_seconds: int = 60
    rpc_fallback: str = ''
    watch_seconds: int = 300
    backfill_tx: int = 3
    # Flow gates
    min_buys: int = 8
    min_buyers: int = 6
    min_net_sol: float = 2.0
    dust_sol: float = 0.05
    max_wallet_share: float = 30.0
    max_bundle_share: float = 50.0
    max_sell_ratio: float = 60.0
    max_lag: int = 30
    max_run_pct: float = 100.0
    # Quality gates
    max_top10_pct: float = 30.0
    max_holder_pct: float = 10.0
    max_dev_pct: float = 10.0
    max_dev_buy_pct: float = 15.0
    max_creator_launches: int = 3
    max_progress: float = 85.0
    require_socials: bool = False
    require_mint_check: bool = True
    min_score: int = 0
    min_liquidity_usd: float = 5000.0
    # Calls and follow-up
    max_calls_per_hour: int = 5
    track_minutes: int = 60
    followups: bool = True

    def rules(self) -> Rules:
        return Rules(min_buys=self.min_buys, min_buyers=self.min_buyers,
                     min_net_sol=self.min_net_sol, dust_sol=self.dust_sol,
                     max_wallet_share=self.max_wallet_share / 100,
                     max_bundle_share=self.max_bundle_share / 100,
                     max_sell_ratio=self.max_sell_ratio / 100, max_lag=self.max_lag,
                     max_run=self.max_run_pct / 100)

    def quality_rules(self) -> QualityRules:
        return QualityRules(self.max_top10_pct, self.max_holder_pct, self.max_dev_pct,
                            self.max_dev_buy_pct, self.max_creator_launches, self.max_progress,
                            self.require_socials, self.min_score)

    def reservoir_rules(self) -> ReservoirRules:
        return ReservoirRules(alive_min_sol=self.alive_min_sol,
                              promote_min_rate=self.promote_min_rate,
                              promote_min_sol=self.promote_min_sol,
                              max_progress=self.max_progress,
                              max_dev_buy_pct=self.max_dev_buy_pct,
                              max_creator_launches=self.max_creator_launches)

    @classmethod
    def load(cls, env: dict | None = None) -> 'Config':
        if env is None:
            from dotenv import load_dotenv
            load_dotenv(ROOT / '.env')
            env = dict(os.environ)
        values = {}
        for name, (key, low, high) in ENV.items():
            raw = str(env.get(key, '')).strip()
            if not raw:
                continue
            kind = type(getattr(cls, name))
            if kind is bool:
                values[name] = raw.lower() in {'1', 'true', 'yes', 'on'}
                continue
            try:
                value = kind(raw)
            except ValueError:
                raise ValueError(f'{key} must be a number') from None
            if not low <= value <= high:
                raise ValueError(f'{key} must be between {low} and {high}')
            values[name] = value
        http = str(env.get('SOLANA_RPC_URL', '')).strip() or cls.rpc_http
        ws = str(env.get('SOLANA_RPC_WS_URL', '')).strip()
        if not ws:
            ws = http.replace('https://', 'wss://', 1) if http != cls.rpc_http else cls.rpc_ws
        if not (http.startswith('https://') and ws.startswith('wss://')):
            raise ValueError('RPC endpoints must use HTTPS and WSS')
        fallback = str(env.get('SOLANA_RPC_FALLBACK_URL', '')).strip()
        if fallback and not fallback.startswith('https://'):
            raise ValueError('Fallback RPC must use HTTPS')
        return cls(rpc_fallback=fallback, tg_token=str(env.get('TELEGRAM_BOT_TOKEN', '')).strip(),
                   tg_chat=str(env.get('TELEGRAM_CHAT_ID', '')).strip(),
                   rpc_http=http, rpc_ws=ws, **values)

    def public(self) -> dict:
        """Settings safe to print: no token, no chat id, no RPC query string (API keys)."""
        hidden = {'tg_token', 'tg_chat', 'db_path', 'rpc_fallback'}
        out = {f.name: getattr(self, f.name) for f in fields(self) if f.name not in hidden}
        out['rpc_http'] = self.rpc_http.split('?')[0]
        out['rpc_ws'] = self.rpc_ws.split('?')[0]
        out['telegram_configured'] = bool(self.tg_token and self.tg_chat)
        return out


# field -> (environment variable, minimum, maximum); booleans ignore the bounds.
ENV = {
    'eval_concurrency': ('EVAL_CONCURRENCY', 1, 8),
    'quiet_seconds': ('QUIET_SECONDS', 30, 300),
    'rpc_per_minute': ('RPC_REQUESTS_PER_MIN', 1, 600),
    'rpc_per_day': ('RPC_REQUESTS_PER_DAY', 100, 1_000_000),
    'dex_interval': ('DEX_SECONDS_BETWEEN', .2, 30),
    'sweep_seconds': ('SWEEP_SECONDS', 10, 600),
    'sweep_accounts': ('SWEEP_ACCOUNTS', 100, 500),
    'reservoir_minutes': ('RESERVOIR_MINUTES', 10, 240),
    'alive_min_sol': ('ALIVE_MIN_SOL', 0, 50),
    'promote_min_rate': ('PROMOTE_MIN_SOL_PER_MIN', 0, 50),
    'promote_min_sol': ('PROMOTE_MIN_SOL', 0, 100),
    'max_watch': ('MAX_ACTIVE_TOKENS', 1, 30),
    'watch_seconds': ('WATCH_SECONDS', 60, 1800),
    'backfill_tx': ('BACKFILL_TX', 0, 30),
    'min_buys': ('MIN_BUYS', 1, 200),
    'min_buyers': ('MIN_UNIQUE_BUYERS', 1, 100),
    'min_net_sol': ('MIN_NET_SOL', 0, 500),
    'dust_sol': ('DUST_SOL', 0, 5),
    'max_wallet_share': ('MAX_WALLET_SHARE_PCT', 1, 100),
    'max_bundle_share': ('MAX_BUNDLE_SHARE_PCT', 0, 100),
    'max_sell_ratio': ('MAX_SELL_RATIO_PCT', 1, 100),
    'max_lag': ('MAX_EVENT_LAG_SECONDS', 5, 300),
    'max_run_pct': ('MAX_RUN_PCT', 5, 1000),
    'max_top10_pct': ('MAX_TOP10_PCT', 1, 100),
    'max_holder_pct': ('MAX_HOLDER_PCT', 1, 100),
    'max_dev_pct': ('MAX_DEV_PCT', 0, 100),
    'max_dev_buy_pct': ('MAX_DEV_BUY_PCT', 0, 100),
    'max_creator_launches': ('MAX_CREATOR_LAUNCHES', 0, 1000),
    'max_progress': ('MAX_PROGRESS_PCT', 1, 100),
    'require_socials': ('REQUIRE_SOCIALS', 0, 1),
    'require_mint_check': ('REQUIRE_MINT_CHECK', 0, 1),
    'min_score': ('MIN_SCORE', 0, 100),
    'min_liquidity_usd': ('MIN_LIQUIDITY_USD', 0, 10_000_000),
    'max_calls_per_hour': ('MAX_CALLS_PER_HOUR', 1, 60),
    'track_minutes': ('TRACK_MINUTES', 5, 240),
    'followups': ('FOLLOWUP_ALERTS', 0, 1),
}
