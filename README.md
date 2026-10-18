# Solter 3.6.0

Pemindai momentum Pump.fun dan PumpSwap yang menerima transaksi live, memilih kandidat berdasarkan bukti pasar terbaru, lalu mengirim alert Telegram berbahasa Indonesia.

## Arsitektur

| Modul | Tugas |
| --- | --- |
| `stream.py` | Menjaga koneksi event transaksi dan pemulihannya |
| `events.py` | Memvalidasi serta menormalkan transaksi |
| `momentum.py` | Menghitung harga, volume, tekanan beli, dan kondisi awal kandidat |
| `risk.py` | Merangkum perilaku wallet dari transaksi yang benar-benar teramati |
| `decision.py` | Menentukan CALL, WATCH, atau REJECT dari bukti terbaru |
| `market.py` | Mengambil data pasar untuk kandidat terpilih, dengan tenggat singkat |
| `social.py` | Meringkas penyebutan alamat kontrak dalam posting Bluesky publik |
| `outcomes.py` | Mencatat keputusan dan harga yang kemudian teramati pada stream |
| `storage.py` | Menyimpan state, riwayat keputusan, dan pengiriman alert dalam SQLite |
| `telegram.py` | Menyiapkan pesan, gambar, tautan, serta tombol Salin CA |
| `app.py` | Mengatur aliran data dan tugas yang berjalan secara independen |

## Pemilihan token

Solter memusatkan evaluasi pada token yang diluncurkan dalam 60 menit terakhir. Kandidat 0–30 menit mendapat persyaratan aktivitas awal; token 30–60 menit harus menunjukkan pembelian yang lebih meyakinkan.

Setiap transaksi masuk memperbarui pengamatan lokal. Jika bukti aktivitas cukup, mesin keputusan memeriksa:

- Tekanan beli dan jual, volume bersih, serta keberlanjutan arus beli dalam 25–45 detik terakhir.
- Apakah harga mulai turun dari puncak terbaru bersamaan dengan dominasi penjualan.
- Apakah pembelian terbaru melemah tajam dibanding periode sebelumnya.
- Konsentrasi pembelian berdasarkan **wallet yang teridentifikasi dalam feed**.
- Risiko penjualan pembuat token yang teramati dan kecukupan likuiditas ketika informasinya tersedia.

**CALL** berarti kriteria pasar yang teramati terpenuhi, bukan jaminan token aman atau akan naik. **WATCH** berarti bukti belum cukup atau momentum melemah dan bisa dievaluasi ulang jika data baru datang. **REJECT** dipakai untuk risiko tertentu yang sudah jelas terdeteksi. Informasi yang belum tersedia tidak dianggap bernilai nol ataupun sebagai bukti keamanan.

Pemeriksaan pasar bagi kandidat potensial berlangsung independen dan memiliki tenggat pendek. Scanner terus menerima transaksi saat pembacaan tambahan berlangsung. Informasi sosial melengkapi alert secara terpisah; cakupannya hanya posting publik Bluesky yang menyebut alamat kontrak secara eksplisit.

## Riwayat hasil keputusan

`outcomes.py` menyimpan harga ketika sebuah kandidat mulai ditinjau, beserta harga tertinggi dan terendah yang **kemudian diterima lewat feed yang sama** selama maksimal 30 menit. Jurnal ini tidak memerlukan request harga tambahan. Riwayat keputusan disimpan di `decision_history` dan tersedia melalui `trace`.

Pengukuran tersebut hanya mencakup transaksi yang berhasil diterima. Puncak harga yang tercatat bukan bukti transaksi dapat dieksekusi pada harga itu, dan bukan laba bersih. Jurnal belum melakukan perubahan aturan otomatis; hasilnya digunakan untuk audit dan kalibrasi berikutnya.

## Instalasi Termux

```sh
python -m pip install -r requirements.txt
cp .env.example .env
nano .env
python bot.py doctor
python bot.py run
```

Isi `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID` di `.env`. Perintah pengamatan:

```sh
python bot.py stats
python bot.py trace --mint ALAMAT_TOKEN
```

`stats` menampilkan jumlah token yang pernah ditinjau, sampel keputusan, dan berapa token memiliki puncak 2× **teramati**. `trace` memperlihatkan alasan keputusan dan jangkauan observasi. File konfigurasi serta database disimpan secara lokal.

## Keandalan dan keterbatasan

Kecepatan dan kelengkapan bergantung pada feed transaksi pihak ketiga. Feed bisa terlambat, terputus, atau kehilangan event. Pengecekan holder sebenarnya, insider, rug probability, dan kemampuan menjual dengan slippage tertentu memerlukan data tambahan yang belum tersedia secara andal pada jalur ini. Jangan menganggap ketiadaan peringatan sebagai bukti token aman.

Untuk memeriksa latensi, amati log `SIGNAL` dan `ALERT ... event_to_delivery_ms`. Evaluasi ketepatan scanner dengan membandingkan call serta token yang terlewat, termasuk pengaruh fee dan slippage.
