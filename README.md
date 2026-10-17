# Solter 3.0 — Clean Momentum Scanner

Solter mencari momentum memecoin Solana dari **sumber pasar publik gratis**, lalu
mengirim **satu alert per contract address (CA)** ke Telegram jika memenuhi
kriteria kualitas pasar. Tidak membeli/menjual token.

> **Penting:** alert **tidak diverifikasi on-chain**. Volume, transaksi, dan
> harga dapat dimanipulasi. Koneksi dan cache API gratis tidak menjamin deteksi
> setiap token atau setiap kenaikan. Data buy/sell adalah jumlah transaksi,
> **bukan jumlah pembeli unik**.

## Dibangun ulang, bukan ditambal

Alur yang dipakai **hanya satu**:

```
PumpPortal launch/migration ──┐
GeckoTerminal new/trending ───┼──> SQLite candidates ──> DEX batch (<=30)
DexScreener profiles/boosts ─┘                             │
                                                          ▼
                                                 Evaluate in memory
                                                  │          │
                                                Accept      Reject
                                                  │          │
                                              Telegram   Record reason
```

- Tidak ada RPC Solana, wallet tracker, backfill, pelacakan milestone 2×,
  perintah `test`, atau mode scanner.
- Tidak ada batas usia token, plafon MC, jumlah kandidat keseluruhan, ataupun
  kuota call per jam buatan.
- Kandidat tidak hilang saat satu batch penuh atau bot direstart.
- 60% kapasitas batch dialokasikan untuk kandidat aktif dan 40% untuk kandidat
  lama yang waktunya diperiksa; kapasitas kosong dapat digunakan pihak lain.
- Pool SOL, USDC, dan USDT resmi dari venue yang dikenali dievaluasi. Token
  yang belum migrasi hanya masuk **discovery**, belum dapat dinilai berdasarkan
  transaksi bonding curve menggunakan feed gratis yang dipakai saat ini.
- Sinyal yang lolos diukur dari transaksi 5 menit, tekanan beli, volume,
  perubahan harga, likuiditas, dan aktivitas relatif terhadap MC. MC besar
  harus menunjukkan volume/likuiditas yang sebanding, bukan dibuang otomatis.
- Alert dikirim dalam tugas terpisah; Telegram tidak menahan discovery.
- Jika Telegram menolak request secara pasti, alert dapat dicoba ulang setelah
  jeda. Jika respons tidak pasti (misalnya timeout setelah pesan mungkin terkirim),
  bot menahan retry otomatis agar tidak menghasilkan pesan ganda.

## Struktur proyek

```
solter/
  addresses.py     Validasi alamat Solana dan quote mint resmi
  settings.py      Hanya Telegram dan lokasi database
  market_data.py   Normalisasi feed dan pool
  http_client.py   Timeout, pacing, dan respons 429
  database.py      Kandidat persisten, histori alert, trace
  signals.py       Aturan momentum yang dapat dibaca manusia
  scanner.py       Discovery + polling + keputusan
  telegram.py      Satu alert profesional, banner opsional
bot.py             CLI kecil: run, doctor, stats, trace
scripts/           Peluncur Termux
```

## Instalasi Termux

```sh
pkg update
pkg install python
python -m pip install -r requirements.txt
cp .env.example .env
nano .env
```

Isi **hanya** `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID`. `.env` lama tetap
bisa dipakai: pengaturan `SCANNER_MODE`, `TRACK_MINUTES`, dan konfigurasi RPC
lainnya **diabaikan** oleh runtime baru.

```sh
python bot.py doctor
python bot.py run
```

Untuk melihat keadaan dan menelusuri satu CA:

```sh
python bot.py stats
python bot.py trace --mint 3NxfWMHmfQg8NJFW5AG1ti2xACdczWz24e9SKyk1pump
```

`trace` membedakan **tidak ditemukan**, **DEX belum terindeks**, **ditolak
filter**, **gagal terkirim**, dan **sudah dikirim**. Jika sumber belum pernah
melihat suatu mint, bot tidak dapat mengetahui kapan token tersebut naik.

## Batasan operasional yang sengaja dipertahankan

1. Pacing request otomatis dan retry cooldown saat 429 untuk menjaga API gratis.
2. Satu alert per token, tanpa spam atau batas call/jam buatan.
3. Tidak menganggap metadata/volume DEX sebagai verifikasi keamanan.
4. Interval refresh aktif ~12 detik pada data yang **sudah terindeks**;
   respons DEX bisa menggunakan cache. Tidak ada klaim live tick-per-tick.
5. Venue yang tidak dikenali dan quote token palsu tidak diterima sebagai
   harga tepercaya. Hal ini membatasi coverage demi validitas data.
6. Jika bot mati atau koneksi WebSocket terputus, sebagian event launch bisa
   terlewat. Feed discovery lain dapat menemukan token kemudian, tetapi
   tidak menjamin pemulihan setiap event yang hilang.

## Upgrade dari v2.x

Baca [UPGRADE.md](UPGRADE.md) **sebelum** mengganti proyek. Database lama
dapat dibaca dan riwayat `calls` dipertahankan agar token yang sudah dipanggil
tidak dikirim ulang. Tabel lama tidak dihapus dari database pengguna.

## Keandalan dan status pemeriksaan

Versi ini **tidak disertai maupun dijalankan dengan unit test**, sesuai
permintaan pengembangan. Koneksi live ke feed dan Telegram hanya dapat
dinyatakan berhasil setelah `python bot.py run` dijalankan dengan kredensial
valid pada perangkat yang memiliki akses internet. Paket tidak berisi token bot.
