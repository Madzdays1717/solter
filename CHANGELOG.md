# Changelog

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
