# Changelog

## 3.0.0 — Clean rewrite

- Replaced legacy multi-mode runtime with a single market scanner.
- Removed RPC, wallet tracking, transaction backfill, milestone tracking,
  hourly alert caps, post-call polling, presets and obsolete test suite.
- Introduced persistent candidate inventory with fair hot/cold scheduling,
  restart-safe state, detailed rejection reasons, and v2 call deduplication.
- Retained free PumpPortal discovery, GeckoTerminal pool discovery and
  DexScreener batch market snapshots.
- Implemented adaptive early/breakout signals and nonblocking Telegram
  delivery with optional source-provided banner (no image download).
- Kept network pacing, deduplication, and data sanity checks.
- No claim of live performance or profitability without production logs.
