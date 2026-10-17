# Analisis: apa yang membuat call memecoin berpotensi

Dokumen ini menjelaskan *kenapa* Solter v2 dibangun seperti ini: apa yang kurang di v1,
apa ciri memecoin yang layak di-call sejak awal, bagaimana setiap ciri diukur **hanya dengan
sumber gratis**, dan batasan yang tetap ada.

> Tidak ada bot yang bisa menjamin call selalu profit. Mayoritas token Pump.fun berakhir
> mendekati nol. Target yang realistis: call yang lebih awal, lebih sedikit rug, dan hasil
> setiap call **terukur** supaya filternya bisa disetel dari data sendiri.

## 1. Kenapa dibangun ulang (bukan ditambal)

| Masalah di v1 | Akibat | Perbaikan di v2 |
|---|---|---|
| Token yang dipantau = 5 slot "siapa datang duluan", 4 menit | Hampir acak; token yang naik di menit ke-10 tidak pernah terlihat | **Reservoir**: semua launch dicatat gratis, disapu 100 bonding curve per request, slot live untuk *inflow tercepat*, ada kesempatan kedua |
| Satu `getTransaction` per trade, budget 1.800/hari dan 30/menit | Token paling ramai justru kehilangan trade (antrean > 35 dtk dibuang) | Trade dibaca dari **log WebSocket** (event `TradeEvent`): **0 request RPC per trade** |
| Hitungan transaksi, bukan uang | 6 buy × 0,01 SOL dari bot = lolos | Semua gerbang dalam **SOL**, buy debu dibuang, dev tidak dihitung |
| Harga wajib dari DexScreener | Telat di menit-menit awal | Harga, market cap, progres dihitung **on-chain** dari reserve |
| Konsentrasi = porsi *jumlah buy* | Holder besar tidak terdeteksi | Top-10 holder, holder terbesar, saldo dev (RPC gratis) |
| Tidak ada pengukuran hasil | Tidak bisa tahu filter bagus atau tidak | Harga 5/15/30/60 mnt, puncak, drawdown, laporan per grade, daftar token yang terlewat |

Hampir semua modul berubah (sumber trade, pemilihan token, sinyal, penyimpanan, pengukuran),
jadi menulis ulang lebih bersih daripada menambal. Bagian v1 yang sudah benar dipakai lagi:
turunan PDA (diverifikasi terhadap alamat Pump yang dikenal), cek mint authority, decoding
bonding curve, budget RPC, dan aturan "satu call per mint, tidak pernah dikirim dua kali".

## 2. Sumber data: semua gratis

| Sumber | Dipakai untuk | Biaya |
|---|---|---|
| PumpPortal WebSocket `subscribeNewToken`, `subscribeMigration` | Setiap launch (mint, creator, buy awal dev, metadata uri) dan migrasi | Gratis |
| Solana RPC publik / free tier (`getMultipleAccounts`) | Sweep reservoir: SOL nyata di bonding curve, 100 token per request | Gratis |
| Solana RPC `logsSubscribe` | Trade live dari log (pembeli, SOL, harga, slot) | Gratis |
| Solana RPC `getAccountInfo`, `getTokenLargestAccounts`, `getTokenAccountsByOwner` | Cek mint, curve/pool, holder, saldo dev | Gratis |
| DexScreener API publik | Harga SOL/USD, pair PumpSwap (likuiditas) | Gratis, tanpa key |
| Metadata token (IPFS) | Link X/Telegram/website, gambar | Gratis |

**Tidak dipakai** karena berbayar atau tidak resmi: PumpPortal trade stream (metered), API
berbayar (Birdeye, paket berbayar provider RPC), Twitter/X API, API GMGN/Axiom yang tidak resmi.

## 3. Ciri memecoin berpotensi dan cara mengukurnya

Angka gerbang di tabel adalah mode **seimbang** (default `CALL_MODE`). Mode `ketat` dan `agresif`
ada di `.env.example`.

| Ciri | Kenapa penting | Cara Solter mengukur (gratis) | Gerbang default |
|---|---|---|---|
| **Uang nyata masuk** | Hanya buy yang menambah SOL di curve; bot volume tidak bisa memalsukannya tanpa modal | Pertumbuhan `real_sol` bonding curve antar sweep (SOL/menit), net inflow dari event | Promosi ≥ 0,4 SOL/mnt dan ≥ 0,8 SOL komunitas; call ≥ 1,5 SOL net dalam 3 mnt |
| **Banyak pembeli berbeda** | Demand yang menyebar lebih tahan daripada 1-2 paus | Pembeli unik dengan buy ≥ 0,05 SOL, creator tidak dihitung | ≥ 6 buy, ≥ 5 pembeli |
| **Momentum masih sekarang** | Call telat = beli puncak | Pembeli aktif 45 dtk terakhir, burst 20 dtk, umur buy terakhir | ≥ 3 aktif, burst ≥ 3, buy terakhir ≤ 30 dtk |
| **Tidak didominasi satu wallet** | Satu wallet dipecah jadi banyak transaksi | Porsi SOL pembeli terbesar | ≤ 35% |
| **Bukan bundle** | Banyak wallet beli di slot yang sama = biasanya satu orang | SOL di slot dengan ≥ 3 wallet berbeda | ≤ 50% |
| **Tekanan jual wajar** | Banyak jual = distribusi | SOL jual / SOL beli | ≤ 70% |
| **Volume aktif nyata** | Koin yang naik diperdagangkan ramai | Volume beli+jual dalam 3 mnt | ≥ 4 SOL |
| **Bukan volume palsu (wash)** | Bot beli lalu jual lagi: volume besar tanpa uang masuk | Porsi volume dari wallet yang beli dan jual dalam jendela yang sama | ≤ 60% |
| **Market cap masih awal** | Terlalu kecil = belum ada tenaga, terlalu besar = sudah telat | MC dari harga on-chain × harga SOL; dicek sebelum dan sesudah cek mendalam | $6 ribu – $80 ribu |
| **Belum terlalu tinggi** | Hindari mengejar candle | Kenaikan harga dalam jendela, progres bonding curve | ≤ +200%, progres ≤ 95% |
| **Holder menyebar** | Top holder besar = risiko dump | `getTokenLargestAccounts`, vault curve/pool dikeluarkan | Top-10 ≤ 35%, terbesar ≤ 12% |
| **Dev bersih** | Dev pegang banyak / jual = rug klasik | Saldo dev sekarang, buy awal dev, dev jual (event) | Dev ≤ 10%, buy awal ≤ 15%, dev jual = blok |
| **Creator bukan serial launcher** | Satu wallet yang meluncurkan banyak token = pola rug | Hitungan launch per creator dari stream discovery | ≤ 3 launch |
| **Kontrak aman** | Mint/freeze authority aktif = bisa dicetak/dibekukan | `getAccountInfo` mint (jsonParsed), ekstensi Token-2022 di-whitelist, Mayhem dikecualikan | Wajib |
| **Ada komunitas** | Narasi/komunitas sering membedakan runner | Link X, Telegram, website dari metadata | Masuk skor; bisa diwajibkan (`REQUIRE_SOCIALS`) |

Yang **tidak bisa** diukur dengan andal secara gratis: sentimen X/Twitter, kualitas narasi,
kluster wallet berdasarkan riwayat pendanaan, dan PnL "smart wallet". Ini sengaja tidak dipalsukan.

## 4. Alur v2

```
PumpPortal (gratis)                    Solana RPC (gratis)
  launch ──► reservoir ──sweep 100/request──► alive? inflow SOL/mnt
                │                                  │
                │                     peringkat inflow tercepat
                ▼                                  ▼
            migrasi ──► pool PumpSwap ──► slot live (logsSubscribe)
                                                   │ trade dari log, 0 RPC
                                                   ▼
                                    gerbang flow (SOL, pembeli, momentum,
                                    bundle, jual, dev jual, overextended)
                                                   │
                                                   ▼
                                    cek mendalam (mint, curve/pool, top holder,
                                    saldo dev, creator, sosial) + skor 0-100
                                                   │
                                                   ▼
                                     1 call Telegram ──► lacak hasil 60 mnt
                                                          (2x/3x/5x/10x, dev jual)
```

## 5. Skor 0-100 (deterministik)

Skor bukan prediksi; ini urutan kualitas setup supaya hasil bisa dibandingkan per grade.

- **Flow (40)**: net SOL (log, penuh di 20 SOL) 15 · pembeli (6→20) 15 · aktif 45 dtk (5 = penuh) 10
- **Distribusi (30)**: top-10 (30%→0, 10%→penuh) 15 · holder terbesar (10%→0, 2%→penuh) 5 · dev (10%→0, 0%→penuh) 10
- **Konteks (30)**: sosial (3 link = penuh) 10 · creator launch pertama 10 / kedua 5 · tekanan jual rendah 5 · tanpa bundle 5

Grade **A ≥ 75**, **B ≥ 60**, **C** di bawahnya. Default `MIN_SCORE=0`: skor tidak menolak token,
hanya memberi label, karena token yang lolos semua filter tapi biasa saja bisa hanya mendapat 40-49.
Naikkan `MIN_SCORE` setelah `report` menunjukkan grade mana yang hasilnya buruk.

## 6. Cara membuktikan dan menyetel

1. Jalankan `python bot.py probe` sekali: memastikan event trade terbaca dari log dan alamat
   pool PumpSwap yang diturunkan cocok, di koneksi Anda sendiri.
2. Biarkan bot berjalan 24-72 jam tanpa mengubah aturan.
3. `python bot.py report`: lihat persentase call yang mencapai 2x/3x/5x, median return per
   horizon, hasil per grade, dan **token yang naik ≥ 3x tanpa call beserta alasannya**.
4. Ubah **satu** ambang dalam satu waktu, berdasarkan alasan yang paling sering membuang runner
   (bagian "Alasan tidak call terbanyak" dan "Token yang naik ≥3x ... tanpa call").
5. Kalau grade C konsisten buruk, naikkan `MIN_SCORE` ke 60.

## 7. Batasan yang tetap ada

- Hasil di laporan belum termasuk fee dan slippage; hasil nyata lebih rendah.
- Sweep membaca setiap token beberapa kali per menit, bukan setiap detik: token yang naik dan
  turun dalam 30 detik bisa terlewat.
- RPC publik bisa lambat atau memutus WebSocket; free tier provider pribadi lebih stabil.
- Layout event Pump bisa berubah. Decoder membaca field lama dengan aman, dan ada fallback
  (inner instruction, lalu selisih saldo), tapi jalankan `probe` setelah ada update besar Pump.
- Pembeli unik bukan berarti orang berbeda; bundle lintas slot dan wallet yang didanai satu
  sumber tidak terdeteksi.

## 8. Langkah berikut (opsional)

- **Mode firehose untuk VPS**: satu `logsSubscribe` pada program Pump membaca *semua* trade
  semua token (gratis, tapi bandwidth besar; tidak cocok untuk HP).
- **Daftar wallet cerdas dari data sendiri**: wallet yang berulang kali membeli awal di token
  yang kemudian 3x+.
- **Kluster pendanaan** untuk pembeli awal (mahal di RPC gratis; hanya untuk kandidat final).
