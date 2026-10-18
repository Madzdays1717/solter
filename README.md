# Solter 3.4.0

Solter adalah pemantau momentum token baru Pump.fun dan PumpSwap. Aplikasi menerima event transaksi, menghitung metrik pasar secara lokal, lalu mengirim alert Telegram bila aktivitas pembelian dan likuiditas yang **teramati** mendukung sinyal. Setiap alert adalah informasi pasar, bukan bukti keamanan atau anjuran membeli.

## Cara kerja

1. `stream.py` menerima event `create`, `buy`, `sell`, dan `migrate` dari feed pihak ketiga.
2. `events.py` memvalidasi format transaksi dan menormalisasi detail pembeli yang tersedia.
3. `momentum.py` dan `risk.py` menghitung tekanan beli/jual, volume, pergerakan harga sejak pengamatan, pembeli teramati, dan aktivitas developer yang dapat dikenali.
4. Ketika ada kandidat yang memenuhi syarat awal, `market.py` mencari snapshot Pump.fun (bonding curve) atau pool PumpSwap di DexScreener. Pemeriksaan ini punya tenggat **maksimal 1,7 detik** untuk setiap kandidat; kandidat lain tidak ikut menunggu. Respons eksternal adalah sumber data tambahan, bukan pengganti kronologi trade live.
5. Mesin menghitung ulang kandidat dari event terbaru, menolak likuiditas sangat rendah atau tekanan jual besar yang *terkonfirmasi*, kemudian `telegram.py` mengirim notifikasi. Aplikasi mencatat status dan alasan di SQLite.

**Sumber data**

- Stream transaksi: koneksi Socket.IO `sol.shrine.trade`; layanan pihak ketiga, sehingga kelengkapan, kuota, dan uptime tidak dijamin.
- Pump.fun frontend API: profil, status pool dan MC yang tersedia untuk koin sebelum migrasi. Endpoint frontend dapat berubah tanpa pemberitahuan.
- DexScreener API: MC, volume, likuiditas dan transaksi pool PumpSwap ketika sudah terindeks. Data snapshot tidak selalu seketika; bot membaca pool PumpSwap dan bukan pasangan DEX sembarang.
- Telegram Bot API: pengiriman alert.

GMGN, Pump.fun, DexScreener dan Solscan disediakan sebagai tautan pada Telegram. GMGN **tidak dipakai untuk mengambil data otomatis** pada versi ini.

## Batas kualitas data

- Top 10 holders, persentase kepemilikan developer, sniper, bundler, dan estimasi rug tidak dapat dibuktikan hanya dari stream trade. Solter tidak menampilkan angka fiktif atau mengklaim token aman.
- MC dari API pasar hanya ditampilkan bila snapshot relevan dengan waktu alert. MC hasil perkalian harga × suplai adalah **perkiraan**, bukan kapitalisasi suplai beredar yang pasti.
- Jika sumber tambahan gagal atau terlambat, alert masih bisa dikirim bila syarat dari transaksi live sudah cukup. Ini menjaga latensi, tetapi risiko token tidak terverifikasi.
- Waktu `event_to_delivery_ms` dihitung dari timestamp sumber hingga respons Telegram; angka bergantung juga pada ketepatan jam sumber.
- Fokus usia token 0–60 menit; perhitungan usia memakai timestamp peluncuran.

## Instalasi Termux

```sh
python -m pip install -r requirements.txt
cp .env.example .env
nano .env
python bot.py doctor
python bot.py run
```

Isi `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID` pada `.env`. Jangan commit `.env` atau folder `data/`.

Perintah diagnosis:

```sh
python bot.py stats
python bot.py trace --mint ALAMAT_TOKEN
```

## Pengukuran performa

Perhatikan log `SIGNAL ... delay=` dan `ALERT ... event_to_delivery_ms=`. Uji dengan beberapa CA yang benar-benar bergerak: bandingkan saat token ditemukan, alasan penolakan, MC pada waktu alert dan data saat dibuka. Jangan menyimpulkan keberhasilan hanya dari jumlah alert.
