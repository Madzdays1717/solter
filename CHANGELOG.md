# Changelog

## v1.0.0 (2026-10-17) — architectural restart

- New bounded free-tier Solana early-flow engine (separate from v0.4.x; does not overwrite old data).
- PumpPortal free token discovery and migration events; live Solana RPC/WebSocket signature tracking on selected candidates.
- Conservative signer token-balance-change classifier with SQLite processed-signature dedup.
- Distinct buyers, buy burst, sustained interest and simple creator-sell safeguard.
- Live market price requirement, indexed price baseline and mint authority checks.
- One CA/one call Telegram, one photo-with-caption OR one plain text, timeout flagged uncertain.
- RPC daily budget, rate limits, WS reconnection, backfill on outage and worker supervisor.
- Initial unit tests, Termux deployment instructions and explicit limitations.

**Validation:** Offline unit tests only; never described as live-profitable or production-qualified.
