# Solter v1.0.0 — Early Flow Intelligence

**A fresh implementation, not an incremental patch to v0.4.x.** Solter continuously discovers newly launched Pump.fun tokens, watches a **bounded** selection via Solana WebSocket, parses actual Solana transactions, and sends a Telegram *early-flow research call* only with recent verified buy activity and sufficient market data. There is **no automatic trading**, private key management, paid feed, or guaranteed return.

## Installation (Termux)

1. Keep the old Solter process **stopped** so it cannot also send Telegram calls.
2. Install Python and a session manager:

   ```bash
   pkg update && pkg install python tmux unzip -y
   termux-setup-storage
   ```
3. Extract the Solter v1 ZIP in a **separate folder**, for example:

   ```bash
   mkdir -p ~/solter-v1
   unzip ~/storage/downloads/solter-v1.0.0.zip -d ~/solter-v1
   cd ~/solter-v1
   python -m pip install -r requirements.txt
   cp .env.example .env
   nano .env
   ```

   Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Never publish `.env` or copy it from the repo. If an existing working `.env` is already available, transfer it privately and add the new options; do not put it in a public ZIP.

4. Check offline tests and Telegram:

   ```bash
   python -m unittest discover -s tests -v
   python bot.py doctor
   python bot.py test
   ```
5. Run in a persistent terminal session:

   ```bash
   termux-wake-lock
   tmux new -s solter
   cd ~/solter-v1 && python bot.py run
   ```

   Detach `Ctrl+B`, then `D`; reattach with `tmux attach -t solter`. In another session: `cd ~/solter-v1 && python bot.py stats`. To stop the service, return to session and use `Ctrl+C`. Prevent Android from battery-optimizing Termux, and avoid killing it. **A wake lock and tmux cannot guarantee 24/7 operation**: the operating system can kill background processes, and Wi-Fi/network access can fail. An always-on VPS is more dependable for 24/7 uptime.

### Optional: restart after phone reboot

Install the **Termux:Boot** companion app from a compatible source, open it once, and run `bash setup-autostart.sh` inside `~/solter-v1`. This installs a best-effort boot script into `~/.termux/boot/start-solter-v1`. It uses a separate `tmux` session called `solter-v1`; connect with `tmux attach -t solter-v1`. Termux:Boot must be allowed to run in the background and Android may still kill the process. To disable autostart, remove the generated boot script. **Do not run the normal and boot sessions simultaneously**; stop the manual `tmux` session before relying on the boot script.

## RPC config (free, usage bounded)

The default Solana public RPC HTTP/WSS URLs allow development but are subject to strict throttling and may not support stable WebSocket operations. For a more reliable **free tier**, bring your own provider endpoint and its corresponding WS endpoint (e.g. Helius Free if its current terms suit you):

```env
SOLANA_RPC_URL=https://YOUR-OWN-HTTPS-RPC-ENDPOINT
SOLANA_RPC_WS_URL=wss://YOUR-OWN-WSS-RPC-ENDPOINT
RPC_REQUESTS_PER_MIN=30
RPC_REQUESTS_PER_DAY=1800
MAX_ACTIVE_TOKENS=5
WATCH_SECONDS=240
```

The caps are **request counts**, not provider credit accounting. Some RPC methods may cost multiple provider credits. **You must check your provider's actual credit cost and reduce `RPC_REQUESTS_PER_DAY` accordingly**. No software can guarantee a provider's free tier or uptime forever. When the local quota is exhausted, deep analysis pauses; free PumpPortal discovery and bot process remain active. The counter resets at UTC midnight. No automatic paid tier activation occurs.

**Do not confuse `solter` (old folder) with `solter-v1` (new folder)**. v1 data lives in `solter-v1/data/solter.sqlite3` and does not overwrite the old database.

## What it actually does

1. One PumpPortal WebSocket subscribes to free `subscribeNewToken` and `subscribeMigration` streams. It never sends `subscribeTokenTrade` or `subscribeAccountTrade` (metered features).
2. A bounded number of recent tokens enter the watchlist. Non-admitted launches are counted, not represented as fully analyzed. Each active candidate receives a Pump bonding-curve PDA (before graduation), or a PumpSwap pair address (after migration).
3. One shared Solana `logsSubscribe` WSS session listens for signatures mentioning watched accounts. HTTP `getSignaturesForAddress` catches up at startup/after interruptions. `getTransaction` parses a conservative **single-signer net token balance change** and stores one buy or sell event per signature, excluding ambiguous transactions.
4. The signal checks recent activity, at least five **distinct wallet addresses**, buy bursts, continued recent buying, no extreme concentration of *buy count* from one wallet, and limits on sell transactions. It is not proof of independent beneficial ownership or wash-trade detection.
5. DexScreener supplies the currently indexed USD token price. A baseline price observed before the call is used to reject a severely overextended move. If price/pair is unavailable, **no call** is sent rather than inventing USD value.
6. For bonding-curve tokens, Solter validates the on-chain mint authorities and bonding curve, excludes confirmed Mayhem, and blocks when a verified creator sells in the observed transaction window. This does not constitute a full rug-pull/security audit.
7. One Telegram message per mint across New and Migrated; photo+caption when Telegram accepts the image URL, otherwise text only. Telegram delivery timeouts are marked **uncertain** and deliberately **not retried** to avoid duplicate messages (manual inspection needed).
8. SQLite persists token/watch state, signatures, calls and daily RPC request count; worker supervisor, reconnect and backoff help recover from ordinary network errors.

## Known limits — read before trusting calls

- **Not exhaustive.** With the default five slots and four-minute watch window, Solter cannot deeply analyze all high-volume launches. The admission policy is simple first available slot, **not** a validated best-token ranking. It can miss the strongest token.
- **Price may index late.** DexScreener can lack a pair in the earliest minutes. Solter requires a usable current USD price, so it can miss the earliest entry, especially before migration.
- **Transaction inference is conservative and incomplete.** Complex versioned/multisigner swaps and balance metadata with missing wallet owners are ignored; token deltas are **not** exact SOL/USD buy amounts. The RPC subscription `mentions` is one pubkey per call, and you cannot track arbitrary thousands of mints for free.
- **Wallet analysis is limited.** Distinct addresses are not necessarily distinct people. There is no verified funding-graph clustering, insider labeling, bought-vs-sold notional analysis, volume manipulation certification, smart-wallet PnL, or universal rug-pull detection. Security unknown remains unknown.
- **After migration**, a new PumpSwap pool and new stream may be indexed later; Solter follows stage internally, but tokens that have expired can be missed or analyzed after the optimum entry.
- **Operational continuity ≠ signal continuity.** The process attempts to keep running, but if a provider fails or the quota exhausts, no reliable trading signal is possible. Track budget, errors and data freshness via `stats` and console logs.
- **No live production validation was performed by this package build.** Offline tests validate parsers, storage, duplicate prevention and message behavior; run paper tracking for 24–72 hours before considering any money at risk.

## Command reference

```bash
python bot.py run        # Persistent watcher
python bot.py stats      # Saved discovery, trade, call and quota counts
python bot.py doctor     # Configuration diagnostic; never prints secrets
python bot.py test       # Sends one Telegram connectivity test
python -m unittest discover -s tests -v
```

## Sources

- [PumpPortal Data API](https://pumpportal.fun/data-api/real-time/)
- [Pump.fun official IDL](https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump.json)
- [Solana logsSubscribe](https://solana.com/docs/rpc/websocket/logssubscribe)
- [Solana getTransaction](https://solana.com/docs/rpc/http/gettransaction)
- [Solana getSignaturesForAddress](https://solana.com/docs/rpc/http/getsignaturesforaddress)
- [DexScreener API](https://docs.dexscreener.com/api/reference)
