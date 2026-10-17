# Solter v2: arsitektur

## Modul

| Modul | Isi |
|---|---|
| `solter/chain.py` | base58, PDA (diverifikasi: Global `4wTV1Ymi…`, event authority `Ce6TQqeH…`), ATA, pool migrasi PumpSwap, decoding BondingCurve/Pool, harga/market cap/progres, cek mint |
| `solter/events.py` | `TradeEvent` Pump dan `BuyEvent`/`SellEvent` PumpSwap dari log (`Program data:`, stack program dibaca ketat) atau inner instruction (`emit_cpi`), fallback selisih saldo |
| `solter/reservoir.py` | Klasifikasi hasil sweep (fresh/alive/dead), laju inflow, kandidat slot live |
| `solter/flow.py` | Gerbang aliran beli dalam SOL |
| `solter/quality.py` | Holder, sosial, skor 0-100, gerbang kualitas |
| `solter/storage.py` | SQLite (WAL): tokens, trades, processed, creators, calls, outcomes, budget, metrics |
| `solter/rpc.py` | JSON-RPC dengan batas per menit/hari dan porsi per jenis kerja |
| `solter/sources.py` | PumpPortal, DexScreener (parsing murni) |
| `solter/telegram.py` | Format pesan (Indonesia), kirim call sekali, balasan lanjutan |
| `solter/engine.py` | Worker asyncio dan `probe` |
| `solter/report.py` | Laporan hasil |

## Worker (`engine.Solter`)

| Worker | Kerja | Biaya |
|---|---|---|
| `discover` | WebSocket PumpPortal: launch → reservoir (alamat curve diturunkan lokal), migrasi → pool PumpSwap diturunkan lokal | gratis |
| `sweep` | `getMultipleAccounts` (100 curve/request, `dataSlice` 115 byte): alive / dead / inflow | ≤ 60% budget |
| `scheduler` | Akhiri watch (waktu habis, sepi 90 dtk, cooldown 5 mnt), isi slot dari kandidat (maks ⅓ slot untuk migrasi) | 0 |
| `logs` | Satu WebSocket Solana, `logsSubscribe` per akun yang dipantau atau dilacak; trade di-decode dari log | 0 |
| `transactions` | `getTransaction` hanya bila log menunjukkan Buy/Sell tanpa event yang terbaca (atau log terpotong) | ≤ 85% |
| `backfill` | Saat token mulai dipantau / setelah putus: `getSignaturesForAddress` (`until` = signature terakhir) + transaksi dalam jendela | ≤ 75% |
| `sol_price` | Harga SOL/USD dari DexScreener tiap 60 dtk | gratis |
| `evaluator` | Tiap 4 dtk: gerbang flow → cek mendalam (cache 45 dtk lolos / 120 dtk gagal) → skor → call | 4 request per cek |
| `tracker` | Return 5/15/30/60 mnt, puncak/low, balasan 2x/3x/5x/10x, peringatan dev jual | hampir 0 |
| `maintenance` | Kedaluwarsa reservoir, retensi, cache, log `STATUS` tiap 5 mnt | 0 |

Setiap worker diawasi: crash → log + restart dengan backoff, bot tetap jalan.

## Status token

```
fresh ──sweep──► alive ──scheduler──► watching ──call──► called (dilacak TRACK_MINUTES)
  │                │  ▲                   │
  └──► dead        │  └──── demote ◄──────┘ (cooldown, maks 3 kali)
                   └──► dead (dumped / stalled)
fresh/alive yang melewati RESERVOIR_MINUTES ──► expired
migrasi: token apa pun (kecuali sudah di-call) ──► alive, stage=migrated, akun = pool
```

## Aturan yang dijaga

- **Satu call per mint**, dicatat sebelum dikirim. Timeout Telegram = `uncertain`, tidak diulang.
  Foto ditolak → satu kali teks.
- **Fail closed**: mint/freeze authority aktif, ekstensi Token-2022 tak dikenal, curve tidak
  terbaca, quote bukan SOL, Mayhem, pool yang base/quote-nya tidak cocok, holder tidak tersedia,
  harga tidak diketahui → tidak ada call.
- **Event hanya dari program yang benar**: baris `Program data:` diatribusikan ke program di
  puncak stack invoke; teks `Program log:` tidak bisa memalsukan invoke. Inner instruction hanya
  dari program itu sendiri dengan tag event Anchor.
- **Harga hasil hanya setelah call**: harga sebelum waktu call diabaikan saat melacak.
- **Creator dari curve** adalah otoritas; jika berbeda dari PumpPortal, data diperbarui dan
  penjualan creator dicek ulang.
- Tidak ada private key, tidak ada eksekusi trade, tidak ada sumber berbayar.

## Penyimpanan

`data/solter-v2.sqlite3`: trade 2 hari, signature diproses 1 hari, token mati/kedaluwarsa 3 hari,
creator 7 hari, call dan hasilnya disimpan permanen (kecil) untuk laporan.

## Uji

`python -m unittest discover -s tests`: decoding event dengan layout IDL resmi (termasuk upaya
pemalsuan log), PDA terhadap alamat mainnet yang dikenal, gerbang flow/kualitas, reservoir,
penyimpanan, engine end-to-end dengan RPC/Telegram/WebSocket palsu, budget, laporan, konfigurasi.
Asumsi live dicek dengan `python bot.py probe`.
