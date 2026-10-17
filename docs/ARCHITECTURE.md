# Solter v1.0.0 — design record / current limitations

## Objective

Emit one well-formed early opportunity alert per mint after observing a recent, sufficiently diverse sequence of buys, when an entry price is verifiable and minimal on-chain security checks pass. **Do not conflate a call with a guaranteed tradeable profit.** A token may subsequently decline sharply or become untradeable.

## Runtime layers

- `solter/service.py` — independent supervised asyncio workers: discovery, RPC logs subscriptions, bonding/pool address selection, catch-up signatures, transaction parser, periodic price baseline, evaluator, maintenance.
- `solter/flow.py` — pure transaction classification and sequential-buy signal; no heuristic scoring claiming future returns.
- `solter/chain.py` — official Pump bonding PDA and prefixed curve metadata plus SPL/Token-2022 safety checks.
- `solter/storage.py` — WAL SQLite; `calls.mint` primary key prevents a second alert across market stages; trade signature dedup and bounded retention; request usage per UTC day.
- `solter/config.py` — explicit no-cost quotas and fail-closed options.

## Signal gate (current MVP; tune only against a labeled corpus)

- Observation window: most recent 150s. Observed buy count ≥ `MIN_BUYS` (default 6).
- Distinct token-balance gaining signer addresses ≥ `MIN_UNIQUE_BUYERS` (default 5). **Distinct public keys are not ownership independence**.
- At least 3 addresses buy in last 45s, plus ≥3 buys within a rolling 20s interval.
- Total buy count ≥ 1.8 × sell count and most active wallet's buy-count share ≤35%.
- Buys span at least 15s (avoid a one-shot launch transaction); all classified trades have acceptable RPC event latency.
- DexScreener verifies stage, pool and a live USD price; price must not have risen >45% or fallen <−30% since the first indexed price observation. This is **an arbitrary protective limit, not predictive evidence**, and may miss good dips or early pumps.
- For Pump.fun bonding: decoded owner matches official program, curve active, no Mayhem, no seen creator sell; token mint and freeze authority revoked, unknown Token-2022 extension blocked.
- For PumpSwap: detected liquidity positive, mint checked.
- `calls` insert happens BEFORE Telegram request. This enforces at-most-one automated call attempt, not exactly-once guaranteed delivery. Timeout → uncertain, no duplicate retry. Image HTTP 400 → single text fallback; other uncertain responses remain unresolved.

## Explicit non-goals in this release

No automatic trading, mempool or transaction priority fees, X social sentiment, assumed insider identification, clustering by funding graph, universally accurate wash trading detection, developer wallet PnL, stop-loss automation, 10x predictions, paid PumpPortal trade streams, or handling every launch under free quotas. Fees and slippage are NOT modeled in the signal evaluator yet.

## Availability and quota

`RPC_REQUESTS_PER_DAY` is a local upper bound on submitted JSON-RPC HTTP **attempts**, reset at 00:00 UTC. Every retry consumes an additional request. WebSocket subscription usage may be separately metered by providers; the tool does not estimate that spending. Avoid providers that charge automatically outside a fixed free plan. If cap exceeded, deep workers pause/skip while discovery remains live; no guarantees on uninterrupted signal coverage.

## Test / acceptance plan before any upgrade

1. Run 24h soak test in Termux and capture `stats` at start/end. Verify WSS reconnect and that memory/database remain bounded.
2. Intentionally disable network for 60 seconds; verify both WebSocket workers reconnect and no duplicate CA call is generated. Observe known missed events.
3. Run production RPC during a low-traffic period and compare 20 observed token transactions against Solana Explorer/confirmed JSONParsed transactions. Measure the fraction classified vs ignored and mean/end-to-end delay.
4. Check 10 random discovery events versus Pump.fun to validate mint, stage and bonding PDA; test PumpSwap pool change across migrations.
5. Run 72h paper evaluation with a **fixed** rule set. Store alert slot/time, contemporaneous executable quote where available, 5/15/30/60 minute outcomes, best favorable and adverse excursion, liquidity, fees and slippage.
6. Treat missing signals and API budget saturation as engineering failures to measure, not an instruction to reduce safety gates blindly.

## Prioritized next iterations

1. **P0** Improve launch admission scheduling (instead of first free slot) using a cheap, verified event-based candidate reservoir; measure missed high-performing launches.
2. **P0** Improve market baselines with on-chain quote verification for very-new tokens, without inventing USD price.
3. **P0** Add instrumented latency budget: event time, RPC receive, trade decoded, signal time, send time; track every drop reason.
4. **P1** Decode Pump.fun/AMM official trade instruction structures to estimate buy/sell notional and price impact robustly (including Token-2022 cases).
5. **P1** Check token concentration excluding known pool reserve accounts, and correlate creator/early buyers with multi-transaction on-chain histories while respecting the free RPC quota.
6. **P1** Build objective slippage-aware paper portfolio and statistical comparison against the v0.4.x baseline on same market interval.
7. **P2** Optional wallet watchlist module (never required for core signal).

No rule above is a verified profitable strategy; optimizing requires held-out data and live execution-cost analysis.
