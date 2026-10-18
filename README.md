# Solter 3.2

Solter memantau momentum token Pump.fun dan PumpSwap pada satu jam pertama sejak token diluncurkan. Transaksi dari satu feed live diproses menjadi ringkasan aktivitas pasar, penilaian risiko berbasis transaksi, dan alert Telegram.

## Alur

```text
Transaction stream
    → Normalisasi event
    → Token aktif (usia 0–60 menit)
    → Momentum + analisis aktivitas wallet
    → Keputusan
    → Telegram
```

Sumber transaksi: [Shrine Advanced Data Stream](https://shrine.trade/pump/data-api/stream/). Provider menyediakan event transaksi dan peluncuran Pump.fun/PumpSwap. Koneksi Socket.IO digunakan sesuai dokumentasinya. Kecepatan, cakupan event, dan kontinuitas layanan bergantung pada provider. Satu koneksi mengalirkan banyak token; Solter tidak membuat request HTTP per transaksi.

## Analisis mandiri

**Momentum**, dihitung dari transaksi selama dua menit terakhir:

- Volume beli dan jual dalam SOL, arus beli bersih, dan persentase perubahan harga.
- Banyaknya transaksi dan wallet pembeli yang dapat diidentifikasi dari rincian trade.
- Market cap dalam SOL dan cadangan pool yang dilaporkan stream.
- Usia token menurut waktu peluncuran, tetap sama ketika migrasi ke PumpSwap.

**Risiko**, dihitung dari data transaksi yang tersedia:

- Porsi volume beli tiga wallet teratas selama jendela dua menit.
- Pembelian dan penjualan oleh alamat pembuat token, bila alamat tersebut diketahui.
- Jumlah pembeli yang terlihat dalam 30 detik awal.
- Jumlah transaksi dengan beberapa wallet berbeda.
- Indikasi kontrol mint dan freeze dari event peluncuran.

Pembelian oleh tiga wallet teratas **bukan** persentase token yang dimiliki tiga holder teratas. Transaksi dengan banyak wallet **bukan bukti** bundling/insider. Wallet yang tidak teridentifikasi dan transfer token di luar trade tidak dapat dipakai untuk menghitung kepemilikan. Solter tidak memberikan status *aman* berdasarkan metrik ini.

Pada stream yang melaporkan rincian tiap swap, evaluator memakai wallet sebenarnya dalam `breakdown`. Bila daftar wallet tidak disertai volume per wallet, volume tidak dibagi rata atau diklaim sebagai kepemilikan wallet.

## Menjalankan di Termux

```sh
pkg update
pkg install python
python -m pip install -r requirements.txt
cp .env.example .env
nano .env
python bot.py doctor
python bot.py run
```

Isi `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID`. Untuk membaca statistik dan jejak token:

```sh
python bot.py stats
python bot.py trace --mint 3NxfWMHmfQg8NJFW5AG1ti2xACdczWz24e9SKyk1pump
```

SQLite menyimpan metadata token, indikator risiko yang dipantau sejak peluncuran, status alert, dan checkpoint stream. Untuk mengurangi operasi disk, keadaan token ditulis berkala. Rolling window trade dua menit hanya berada di memori dan akan terkumpul kembali setelah restart.

## Struktur

```text
bot.py                CLI
solter/app.py         Pengatur proses
solter/stream.py      Koneksi Socket.IO
solter/events.py      Parser dan validasi data
solter/momentum.py    Status token dan evaluator momentum
solter/risk.py        Ringkasan risiko berbasis transaksi
solter/storage.py     Penyimpanan SQLite
solter/telegram.py    Format dan pengiriman pesan
solter/config.py      Pengaturan operator
solter/addresses.py   Validasi alamat
```

## Operasional

- Pesan alert memuat status risiko dari transaksi yang teramati, bukan sertifikasi keamanan token.
- Aliran ulang `sinceSlot` membantu pemulihan koneksi, tetapi event yang hilang dari upstream tetap mungkin tidak tertangkap.
- Token yang belum terlihat event peluncurannya dapat dicari terbatas melalui metadata Pump.fun secara terpisah. Ketersediaannya mengikuti penyedia layanan.
- Terminal Android dapat menghentikan aplikasi saat perangkat memasuki mode hemat daya; gunakan pengaturan Termux yang sesuai jika menjalankan 24 jam.
- Alert hanya informasi. Solter tidak melakukan pembelian atau penjualan otomatis.

## Verifikasi

Startup lokal dapat dijalankan tanpa kredensial real. Koneksi stream nyata, kelengkapan event, dan pengiriman Telegram harus diperiksa pada perangkat dengan jaringan dan token bot yang aktif. Uji otomatis tidak dijalankan pada pembaruan ini.
