## v2.4.0 — market-only fast path, no Solana RPC by default (2026-10-18)

- `SCANNER_MODE=market` becomes the default: no RPC client, sweep, holder queries,
  Solana WebSocket, transaction backfill, log decoding or RPC-dependent call gating
  in `bot.py run`. `chain` retains the v2.3.0 pipeline.
- On each free DexScreener batch response, evaluate momentum and directly deliver
  a clearly labeled **UNVERIFIED MARKET ALERT** (not a verified on-chain call).
  Gate by real 5m transaction counts (not unique buyers), buy/sell ratio, 5m
  volume and price change, liquidity and MC; avoid obvious sell-dominated pools.
- Free GeckoTerminal trending/new pool discovery, plus fresh PumpPortal migration
  event wakes a DEX batch without waiting for the 65s scan. Revisit recent
  moving/unindexed migration pools around every 15s, retry missing pools 3min.
- Only canonical PumpSwap migrations with SOL quote accepted in market mode.
- Alerts are deduplicated and capped hourly by SQLite. Grade `M` distinguishes
  unverified momentum from A/B/C safety ratings. Reporting and follow-ups don't
  invent a fully diluted supply or unique wallets for these alerts.
- `probe` follows the selected mode; in market mode it uses market HTTP only.
- All changes unit tested offline; free API cache, rate limits and response
  latency prevent any guarantee of instantaneous delivery or complete coverage.

## v2.3.0 — event-first decisions, without evaluator tick (2026-10-18)

- A decoded live trade immediately schedules its own per-mint evaluation task:
  no waiting for the periodic evaluator tick, other mints or a batch barrier.
- Coalesce fast consecutive events for the *same* mint, never discard an event
  arriving while its RPC checks are still running: a changed revision triggers
  another fresh evaluation. Always recheck live flow before a call.
- Discovery, migration, account sweep and eligible DEX market updates wake the
  watch-slot scheduler. Subscription syncing checks every ~200ms rather than
  potentially waiting ~2 seconds in a quiet WebSocket connection.
- Do not serialize decisions behind an extra global 4-token check lane. Allow
  up to min(16, max_watch) concurrent token verifications (minimum 4) while
  respecting the configured real provider RPC minute/day limits and quotas.
- Metadata/IPFS/socials are prefetched separately for watched tokens, and no
  longer hold default security checks. REQUIRE_SOCIALS=1 still explicitly
  fetches socials as a gate. Mint, market/pool, holder and dev checks remain.
- Cache validated DexScreener pair facts from discovery for reuse by a fresh
  migrated-token decision; existing refresh TTL is unchanged.
- Log FLOW verification entry and CALL event-to-delivery milliseconds for
  diagnosing delays (the latter measures from latest received live trade).
- Added event-dispatch, replay, scheduler wakeup, optional metadata latency and
  market cache tests; **92 offline tests pass**. No sustained mainnet trial.
- Limitations: still only watches a bounded set of pools; new/old token discovery
  may be delayed by free API refreshes, RPC request ceilings and log decoding.
  No paid PumpPortal subscribeTokenTrade enabled; no guarantees of coverage,
  instantaneous Telegram delivery or profitability.

## v2.2.3 — non-blocking real-time evaluation (2026-10-18)

- Replace the `gather()` evaluation barrier with independent per-mint jobs. A
  slow RPC for one token no longer delays fast flow checks for other watches.
- Wake the evaluator immediately when new trades arrive; periodic checks still
  refresh the flow even when the stream is quiet.
- Cap simultaneous deep verifications at four, but do not throttle the local
  flow gate on those remote requests.
- RPC throttling no longer sleeps while holding the shared lock. Safety/decision
  calls have priority over queued sweep/backfill/fallback when request slots
  become free; all configured rate and daily ceilings remain enforced.
- Reconfirm live flow after deep checks so a delayed RPC response cannot send a
  call on a surge that already ended.
- Added concurrency and decision-priority regression tests; 88 tests pass
  offline. No live-mainnet latency/performance measurement or profit guarantee.

## v2.2.2 — holder RPC transient retry + call funnel (2026-10-18)

- Recheck `holders unavailable` after 20s rather than caching the transient
  RPC failure for 120s; preserves safety: no holder data still means no call.
- New counters `flow_confirmed`, `holders_rpc_unavailable`, `quality_confirmed`
  help distinguish lack of momentum, unavailable RPC data, and blocked deep checks.
- Added regression test for unavailable→available holder RPC within one momentum
  window. No claim of live-mainnet success or profitable performance.

## v2.2.1 — Agave RPC cursor fix (2026-10-18)

- **Fix RPC `getSignaturesForAddress -32020`**: stop using the saved last trade
  signature as an `until` cursor for backfill; it may belong to an old bonding
  curve or previous pool, or the current provider may not retain it.
- Backfill starts from the most recent signatures, deduplicates with SQLite,
  selects up to `BACKFILL_TX` most recent unprocessed and timely transactions,
  and ingests oldest-to-newest so the latest price is preserved. Signature list
  reads are bounded at 100, fetched transactions still obey the configured limit.
- General-purpose RPC client gracefully retries once without invalid
  `before`/`until` on `-32020`, while accounting for both RPC requests.
- Migrated-token WATCH logs display the 5-minute DEX volume and buy/sell counts
  as preliminary activity, not the uninitialized on-chain SOL inflow.
- Added regression tests; this is a local code-level fix, not a guarantee of
  RPC live availability or profitable token alerts.

## v2.2.0 — momentum-first scanner (2026-10-18)

- GeckoTerminal 5m trending/new-pool discovery, DexScreener profile/boost discovery-only,
  DexScreener 30-mint batch 5m snapshots and rotating known migrated-token inventory.
- Reawaken previously expired migrated tokens on fresh liquidity, buy pressure, volume and
  price data, without automatic age exclusion. Calls still require on-chain flow and deep checks.
- Faster first check, fair sweep queue, fewer irreversible quiet-token rejections,
  dynamic live-slot preemption, concurrent evaluator and independent deep-check requests.
- 12 live slots by default, `cepat` preset in .env example, 12 calls/hour ceiling (not quota),
  expanded market cap window in fast mode; configurable scanner cadence/thresholds.
- SQLite schema migration for market snapshots without losing calls, batch database writes,
  improved provider key redaction in doctor, optional PumpPortal data API key.
- Added market simulations and older-token revival tests; unit tests remain offline.
- Limitation: no live mainnet performance or profitability validation included.

# Changelog

## v2.1.0 (2026-10-17): volume, market cap, dan CALL_MODE

- **Volume aktif**: volume beli+jual dalam jendela 3 menit harus cukup (`MIN_VOLUME_SOL`).
- **Anti wash trading**: porsi volume dari wallet yang beli dan jual dalam jendela yang sama dibatasi
  (`MAX_CHURN_PCT`). Aturan "net/volume" sengaja tidak dipakai karena sama dengan `MAX_SELL_RATIO_PCT`.
- **Rentang market cap USD** (`MIN_MCAP_USD`, `MAX_MCAP_USD`): dicek dari harga trade terakhir
  sebelum cek mendalam (hemat RPC), lalu sekali lagi dari harga on-chain. Tidak berlaku saat harga SOL
  belum diketahui.
- **`CALL_MODE` = ketat / seimbang / agresif**: satu pengaturan untuk semua ambang call. Default baru
  `seimbang` lebih longgar dari v2.0.x (`ketat` = nilai lama), karena call tidak kunjung datang dan
  koin yang naik kencang ditolak sebagai "overextended" (+100%/3 mnt) atau "late on curve" (85%).
- Log `STATUS` tiap 5 menit: corong, alasan penolakan terbanyak, dan peringatan `CEK:` (PumpPortal
  diam, WebSocket tanpa trade, jatah RPC habis).
- Pesan call menampilkan volume 3 menit.

## v2.0.1 (2026-10-17): perbaikan hasil audit "call tidak datang"

- **Budget RPC per jenis kerja.** Sebelumnya semua jenis memakai satu penghitung harian, dan sweep
  (satu-satunya pencari token baru) yang pertama dihentikan di 60%. Akibatnya beberapa jam setelah
  07:00 WIB tidak ada token baru yang dipantau sampai hari berikutnya. Sekarang setiap jenis punya
  jatah tercadang (sweep 45%, keputusan 25%, backfill 15%, fallback 15%) dan hanya boleh meminjam
  sisa yang tidak dibutuhkan jenis lain. Tabel `spend` menggantikan `budget`.
- `BACKFILL_TX` default 8 → 3. Bacaan sweep yang sudah basi tidak bisa lagi mempromosikan token.
- **Respons HTTP dibaca utuh.** `read(n)` aiohttp bisa mengembalikan sebagian body, jadi JSON
  DexScreener/metadata sering gagal: token migrasi selalu ditolak ("thin liquidity"), sosial tidak
  terbaca, harga USD kosong. Metadata yang gagal dicoba lagi setelah 5 menit.
- **Call tidak lagi bergantung pada server gambar.** Dulu `sendPhoto` dengan URL IPFS bisa timeout
  sehingga call dianggap terkirim tapi tidak pernah muncul. Sekarang satu `sendMessage` dengan
  gambar sebagai pratinjau link.
- `MIN_SCORE` default 50 → 0: token yang lolos semua filter tetap di-call dengan label grade.
- Koreksi jam: aliran beli dinilai dengan jam blockchain bila jam HP meleset > 5 detik, dengan peringatan.

## v2.0.0 (2026-10-17): dibangun ulang

- **Reservoir**: semua launch PumpPortal dicatat; sweep `getMultipleAccounts` (100 bonding curve
  per request) mengukur SOL nyata yang masuk; slot live untuk inflow tercepat, kesempatan kedua
  dengan cooldown. Menggantikan "slot pertama yang kosong".
- **Trade tanpa biaya RPC**: `TradeEvent` Pump dan `BuyEvent`/`SellEvent` PumpSwap di-decode dari
  log `logsSubscribe` (stack program dibaca ketat), fallback inner instruction lalu selisih saldo.
- **Sinyal dalam SOL**: net inflow, pembeli non-debu, porsi SOL wallet terbesar, bundle satu slot,
  tekanan jual, kesegaran, overextended, creator tidak dihitung dan penjualannya memblokir.
- **Harga on-chain** dari reserve; alamat pool PumpSwap diturunkan sendiri (tanpa menunggu DexScreener).
- **Kualitas**: top-10 holder dan holder terbesar (vault dikecualikan), saldo dev, buy awal dev,
  serial creator, sosial dari metadata, skor 0-100 dan grade A/B/C.
- **Hasil terukur**: return 5/15/30/60 mnt, puncak, drawdown, balasan 2x/3x/5x/10x dan dev jual,
  `python bot.py report` (per grade, corong, alasan, token yang terlewat).
- `python bot.py probe` untuk memverifikasi asumsi live di koneksi pengguna.
- Budget RPC per jenis kerja; default 60/menit dan 15.000/hari (RPC publik gratis).
- Database baru `data/solter-v2.sqlite3`; database v1 tidak disentuh.
- Dokumentasi berbahasa Indonesia: README, docs/ANALISIS.md, docs/ARCHITECTURE.md.
- HOTFIX.md v1.0.1 dihapus; perbaikannya (`maxSupportedTransactionVersion: 1`) tetap dipakai.

## v1.0.1

- `getTransaction` memakai `maxSupportedTransactionVersion: 1` (error RPC -32015 pada transaksi versi 1).

## v1.0.0

- Watcher early-flow pertama: discovery PumpPortal, `logsSubscribe` per kandidat, klasifikasi
  selisih saldo, sinyal berbasis hitungan, harga DexScreener, satu call Telegram per mint.
