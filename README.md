# Solter v2: caller memecoin awal, 100% sumber gratis

Solter memantau setiap token baru Pump.fun, memilih yang uang masuknya paling cepat, membaca
trade-nya langsung dari log Solana, lalu mengirim **satu** call Telegram hanya jika aliran beli
nyata (dalam SOL), distribusi holder, dev, creator, dan kontraknya lolos. Setiap call dilacak
60 menit (2x/3x/5x/10x, dev jual) supaya kualitas filter bisa dibuktikan dan disetel.

- **Gratis**: PumpPortal (launch & migrasi), RPC Solana publik/free tier, DexScreener, metadata IPFS.
  Tanpa langganan, tanpa API key wajib.
- **Tidak ada trading otomatis** dan tidak ada private key.
- **Bukan jaminan profit.** Baca [docs/ANALISIS.md](docs/ANALISIS.md) untuk alasan setiap
  filter dan batasannya.

## Apa yang baru di v2

| | v1 | v2 |
|---|---|---|
| Pilih token | 5 slot siapa datang duluan | Reservoir semua launch, slot untuk inflow SOL tercepat, kesempatan kedua |
| Data trade | `getTransaction` per trade (budget habis) | Dibaca dari log WebSocket: 0 request per trade |
| Sinyal | Hitungan transaksi | SOL bersih, pembeli non-debu, porsi wallet, bundle, tekanan jual |
| Harga | Wajib DexScreener (telat) | On-chain dari reserve bonding curve |
| Anti-rug | Mint authority, dev jual | + top-10 holder, holder terbesar, saldo dev, buy awal dev, serial creator, sosial |
| Hasil | Tidak diukur | 5/15/30/60 mnt, puncak, drawdown, laporan per grade, token terlewat |
| Migrasi | Tunggu DexScreener | Alamat pool PumpSwap diturunkan sendiri |

## Instalasi (Termux)

1. Hentikan Solter lama supaya tidak ada dua bot yang mengirim call.
2. Pasang Python dan tmux:

   ```bash
   pkg update && pkg install python tmux git -y
   ```
3. Ambil kode dan pasang dependensi:

   ```bash
   git clone https://github.com/madzdays1717/solter.git ~/solter-v2
   cd ~/solter-v2
   python -m pip install -r requirements.txt
   cp .env.example .env
   nano .env        # isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID
   ```

   Jangan pernah membagikan `.env`. Kalau memakai `.env` dari v1, salin hanya token Telegram
   dan URL RPC; nilai lain sebaiknya dari `.env.example` yang baru (v1 memakai batas RPC yang
   jauh lebih kecil).
4. Cek:

   ```bash
   python -m unittest discover -s tests
   python bot.py doctor     # konfigurasi, tanpa menampilkan rahasia
   python bot.py test       # pesan uji ke Telegram
   python bot.py probe      # cek live: event trade di log, pool PumpSwap, harga SOL, PumpPortal
   ```

   Hasil `probe` yang sehat: `pump_trade_events.from_logs` > 0, `pumpswap_pools.canonical_match`
   sama dengan `checked`, `sol_usd_dexscreener` berisi angka, `pumpportal_launch` berisi token.
5. Jalankan di sesi yang tetap hidup:

   ```bash
   termux-wake-lock
   tmux new -s solter
   cd ~/solter-v2 && python bot.py run
   ```

   Keluar dari tmux: `Ctrl+B` lalu `D`. Masuk lagi: `tmux attach -t solter`. Berhenti: `Ctrl+C`.
   Android tetap bisa mematikan proses di latar belakang; VPS lebih andal untuk 24/7.

Opsional: autostart setelah HP restart dengan Termux:Boot: `bash setup-autostart.sh`
(sesi tmux `solter-v2`). Jangan jalankan sesi manual dan sesi boot bersamaan.

Data v2 ada di `data/solter-v2.sqlite3` dan tidak menyentuh database v1.

## Perintah

```bash
python bot.py run         # bot
python bot.py report      # hasil call 7 hari (--days 30 untuk 30 hari)
python bot.py stats       # jumlah token per status, call, metrik, RPC hari ini
python bot.py doctor      # konfigurasi
python bot.py test        # uji Telegram
python bot.py probe       # uji asumsi live di koneksi Anda (~15 request RPC)
```

## Contoh call

```
⚡ SOLTER CALL · Grade A · 82/100
Nama Koin ($SYM)

🧪 Bonding curve 34% · umur 6 mnt
💰 MC $12.3K (82 SOL) · harga $0.00001234
📈 Net inflow +6.4 SOL · vol 9.8 SOL /3 mnt · harga +38%
👥 14 pembeli · 6 aktif 45 dtk · burst 5/20 dtk
🐳 Top10 18.5% · terbesar 4.1% · dev 2.0%
👨‍💻 Dev: launch ke-1 · beli awal 3.2% · belum terlihat jual
🔗 X · Telegram · Web
🛡️ Mint & freeze authority dicabut
```

Gambar token tampil sebagai pratinjau link di atas pesan. Pesan tetap terkirim walaupun server
gambar (IPFS) lambat.

Balasan otomatis di bawah call: `🚀 SYM 2.1x sejak call` (2x, 3x, 5x, 10x) dan peringatan jika
dev menjual setelah call (`FOLLOWUP_ALERTS=false` untuk mematikan).

## RPC gratis dan budget

Default memakai RPC publik `api.mainnet-beta.solana.com` (gratis, tanpa daftar). Untuk koneksi
yang lebih stabil, pakai endpoint free tier milik Anda sendiri (HTTPS + WSS) di `.env`.

Solter menjaga batasnya sendiri: `RPC_REQUESTS_PER_MIN` (default 60) dan
`RPC_REQUESTS_PER_DAY` (default 15.000, reset 00:00 UTC = 07:00 WIB). Setiap jenis kerja punya
jatah yang dicadangkan:

- sweep reservoir (mencari token yang mulai ramai): 45%, intervalnya otomatis melambat jika budget kecil
- cek mendalam sebelum call dan pelacakan: 25%
- backfill trade saat token mulai dipantau: 15%
- fallback transaksi: 15%

Satu jenis kerja boleh memakai lebih dari jatahnya hanya dari sisa yang tidak lagi dibutuhkan jenis
lain untuk sisa hari itu. Jadi backfill atau fallback tidak bisa menghabiskan jatah sweep, dan
pencarian token baru tidak berhenti di tengah hari. Pemakaian per jenis terlihat di
`python bot.py stats` (`rpc_today_by_kind`).

Trade live **tidak** memakai budget karena dibaca dari log WebSocket. Jika provider Anda punya
kuota kredit, sesuaikan `RPC_REQUESTS_PER_DAY` dengan biaya kredit per metode di provider itu.

## Menyetel filter

Cara termudah: satu baris `CALL_MODE` di `.env`.

| Mode | Untuk | Contoh aturan |
|---|---|---|
| `ketat` | Sedikit call, filter paling keras | ≥ 8 buy, net ≥ 2 SOL, MC $8K–$40K, harga naik ≤ 100%/3 mnt |
| `seimbang` (default) | Tengah-tengah | ≥ 6 buy, net ≥ 1,5 SOL, vol ≥ 4 SOL, MC $6K–$80K, naik ≤ 200% |
| `agresif` | Lebih banyak call, lebih banyak juga yang gagal | ≥ 5 buy, net ≥ 1 SOL, vol ≥ 3 SOL, MC $5K–$150K, naik ≤ 400% |

Setiap mode juga memakai aturan volume dan market cap:
- **Volume aktif** dalam 3 menit harus cukup.
- **Volume palsu** ditolak: kalau sebagian besar volume berasal dari wallet yang beli lalu jual lagi
  dalam 3 menit yang sama, itu bot (wash trading), bukan permintaan nyata.
- **Market cap** harus di dalam rentang mode itu, dicek dari harga on-chain sebelum RPC dipakai.

Angka satu per satu tetap bisa ditimpa: hapus tanda `#` di bagian bawah `.env.example`. Angka yang
ditulis di `.env` selalu mengalahkan mode, jadi `.env` lama yang berisi semua angka akan mengabaikan
`CALL_MODE`. Buat `.env` baru dari `.env.example`.

**Membaca log.** Tiap 5 menit bot menulis satu baris `STATUS` berisi corong (launch → hidup →
dipantau → trade terbaca → call) dan tiga alasan penolakan terbanyak, misalnya
`ditolak: few buyers 12, mcap too high 4`. Kalau ada yang rusak (PumpPortal diam, WebSocket tidak
memberi trade, jatah RPC habis), muncul baris `CEK:` dengan sarannya.

Cara menyetel:

1. Jalankan 24-72 jam.
2. `python bot.py report`: lihat hit rate per grade dan bagian *"Token yang naik ≥3x ... tanpa call"*
   beserta alasannya.
3. Kalau alasan terbanyak selalu sama, longgarkan aturan itu saja, atau pindah mode.

## Batasan

- Laporan belum memasukkan fee dan slippage.
- Token yang naik-turun dalam hitungan detik di antara dua sweep bisa terlewat.
- Pembeli unik ≠ orang berbeda; kluster pendanaan wallet tidak dianalisis.
- RPC publik bisa throttle atau memutus WebSocket; status terlihat di log `STATUS` tiap 5 menit.
- Jalankan `probe` lagi bila Pump.fun memperbarui programnya.

Dokumentasi teknis: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Sumber

- [Pump public docs & IDL](https://github.com/pump-fun/pump-public-docs) (pump.json, pump_amm.json)
- [PumpPortal Data API](https://pumpportal.fun/data-api/real-time/)
- [Solana RPC: logsSubscribe](https://solana.com/docs/rpc/websocket/logssubscribe),
  [getMultipleAccounts](https://solana.com/docs/rpc/http/getmultipleaccounts),
  [getTokenLargestAccounts](https://solana.com/docs/rpc/http/gettokenlargestaccounts)
- [DexScreener API](https://docs.dexscreener.com/api/reference)
