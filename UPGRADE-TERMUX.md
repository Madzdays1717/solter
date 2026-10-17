# Upgrade Solter v2.1.0 → v2.2.0 (Termux)

1. Stop the old bot (`Ctrl+C` inside its tmux session). Never run two bot processes against one SQLite database.
2. Backup `.env` and `data/` (especially `data/solter-v2.sqlite3`). Do not post or commit Telegram or RPC keys.
3. Extract this archive and replace only the source, scripts, README, `.env.example`, and tests in the existing project. Keep your existing `.env` and `data/` folder.
4. Update your existing `.env` values to use the new defaults, at minimum:

   ```dotenv
   CALL_MODE=cepat
   MAX_ACTIVE_TOKENS=12
   MAX_CALLS_PER_HOUR=12
   RESERVOIR_MINUTES=720
   MARKET_ENABLED=true
   MARKET_SCAN_SECONDS=65
   MARKET_MIN_BUYS_5M=7
   MARKET_MIN_VOLUME_USD_5M=650
   ```

   If your `.env` contains old overrides like `MIN_MCAP_USD` / `MAX_MCAP_USD`, these win over the fast-mode preset. Remove/adjust them deliberately. Optionally set `PUMPPORTAL_API_KEY` if your PumpPortal access requires a data key. It is not a trading private key.
5. Install dependencies and test:

   ```sh
   python -m pip install -r requirements.txt
   python -m unittest discover -s tests
   python bot.py doctor
   python bot.py stats
   python bot.py run
   ```

The database is migrated automatically. Metrics to watch: `market_discovered`, `market_updated`, `market_gecko_unavailable`, `market_dex_unavailable`, `promoted`, `call_sent`, `no_call:*`, `ws_subscription_refused`, `sweep_paused`, `evaluation_no_budget`. Launch/migration stream problems: `portal_connect`, `launch_seen`, `migration_seen`.

**Limitations:** Pools must be supported PumpSwap pools, on-chain trades need RPC logs, and public APIs may lag. No implementation can guarantee capture of all fast-moving memecoins or profitable entries. This archive is not a Git push; sync it to your repository separately if you deploy from GitHub.
