# Solter 3.5.0

Scanner momentum untuk token Pump.fun dan PumpSwap dengan analisis transaksi lokal serta pengamatan sosial berbasis bukti. Solter berjalan di Termux dengan sumber data publik dan mengirim sinyal ke Telegram. Sinyal bukan rekomendasi investasi maupun jaminan token aman.

## Alur kerja

1. `stream.py` menerima event launch, trade, dan migrasi.
2. `events.py` memvalidasi dan menormalkan event, termasuk trader yang tercantum pada feed.
3. `momentum.py` menghitung tekanan beli, volume dan pergerakan harga. `risk.py` meringkas aktivitas wallet **yang teramati**; tidak mengarang saldo holder atau identitas insider.
4. `market.py` meminta snapshot untuk kandidat yang memenuhi syarat, dengan tenggat pendek. Data terlambat tidak menghambat penerimaan transaksi lain.
5. `social.py` mengumpulkan **postingan Bluesky yang menyebut CA secara eksplisit** untuk kandidat prioritas. Jumlah posting, penulis unik, perkembangan per interval, serta kemiripan teks dihitung lokal. Pencarian berjalan pada worker terpisah; feed sosial tidak menentukan boleh/tidaknya alert.
6. `telegram.py` mengirim alert berbahasa Indonesia. Apabila bukti sosial tiba setelah alert, Solter dapat memperbarui pesan yang sama.
7. `storage.py` menyimpan peluncuran, riwayat alert, alasan penyaringan, dan ringkasan pengamatan sosial dalam SQLite.

## Cakupan sosial dan batas analisis

Solter mengamati sebagian posting publik **Bluesky**, bukan keseluruhan X/Twitter, Telegram, TikTok, atau seluruh internet. Postingan harus memuat alamat kontrak yang sama agar tidak tertukar dengan token berticker serupa. Banyaknya posting di feed terbatas **tidak dapat dipakai sebagai estimasi jumlah seluruh posting di internet**.

- Status `Pembicaraan terpantau` menunjukkan adanya penyebutan CA pada sampel.
- `Perhatian meningkat` dan `Perhatian mereda` adalah perubahan **dalam sampel terbatas**, bukan klaim meme sedang viral secara global.
- `Pola promosi serupa` berarti sebagian posting memiliki teks yang hampir identik; bukan bukti bot, insider, maupun manipulasi terkoordinasi.
- Tidak ditemukannya posting **bukan** bukti token tidak viral. Jika API sosial memblokir atau membatasi pencarian, analisis pasar dan Telegram tetap berfungsi.
- Tidak ada inferensi top holder, persen developer holder, rug probability, ataupun jaminan sell pada harga tertentu. Analisis saldo yang akurat memerlukan data on-chain tambahan.

Pencarian sosial dibatasi satu request sekitar 10 detik, maksimum 120 kandidat tertunda, dan tidak menganalisis token yang sudah kehilangan relevansi dalam antrean. Setiap token diberi jeda minimal 60 detik antar pencarian. Saat penyedia merespons 429/403, pencarian ditunda sementara.

## Instalasi Termux

```sh
python -m pip install -r requirements.txt
cp .env.example .env
nano .env
python bot.py doctor
python bot.py run
```

Isi `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID` dalam `.env`. Jalankan `python bot.py stats` untuk metrik database, dan `python bot.py trace --mint ALAMAT_TOKEN` untuk riwayat token beserta ringkasan sosial teramati. Simpan `.env` dan `data/` hanya di perangkat pribadi.

## Keandalan

Provider transaksi Pump.fun/PumpSwap merupakan pihak ketiga sehingga akses, kuota, dan kelengkapan tidak dijamin. Sumber pencarian Bluesky juga dapat menerapkan batas akses. Untuk pengukuran keterlambatan, perhatikan log `SIGNAL` dan `ALERT ... event_to_delivery_ms`. Evaluasi kualitas alert dengan histori token yang lolos **dan** yang gagal.
