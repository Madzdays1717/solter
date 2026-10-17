# v2.0.2

Evaluasi paralel terbatas, cache kegagalan sementara 10 detik, prefetch kandidat aktif, pemeriksaan momentum ulang setelah RPC, retry/fallback HTTP opsional, log penolakan/durasi, default 16 slot dan quiet rotation 60 detik. Lihat UPGRADE-v2.0.2.md.

# Changelog

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

## v2.1.0 — Radar Volume/MC Aktif
- Radar gratis DexScreener 30 alamat per request, independen dari slot logsSubscribe dan quality-check.
- Deteksi volume 5m, buy/sell, price momentum, kenaikan MC, fallback FDV berlabel estimasi.
- Dedup radar dengan batas terpisah dari CALL terverifikasi, plus 429 adaptive cooldown.
- FIFO token yang belum disapu, pemeriksaan awal dari 45s ke 10s.
- Skema SQLite lama ditambah tiga kolom tanpa menghapus history.
- Empat tes radar tambahan; 83 tes total lulus.
