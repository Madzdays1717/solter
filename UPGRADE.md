# Pembaruan dari Solter v3.4

1. Hentikan bot dengan `Ctrl+C`.
2. Cadangkan `.env` dan folder `data/` dari proyek lama.
3. Ekstrak arsip ini. Ganti seluruh file `solter/*.py` dan `bot.py` dari paket ini di proyek Anda. Jangan menyalin `.env.example` menjadi `.env` jika sudah ada konfigurasi pribadi.
4. Instal dependensi dan jalankan:

```sh
python -m pip install -r requirements.txt
python bot.py doctor
python bot.py run
```

Tidak diperlukan API key sosial. Database akan menambah tabel `social_observations` jika belum ada. Riwayat call lama tetap dipertahankan. Setelah bot menerima transaksi, gunakan `python bot.py trace --mint ALAMAT_TOKEN` untuk melihat informasi sosial yang berhasil dikumpulkan.

Jika Bluesky tidak memberikan hasil atau mengembalikan 403/429, itu bukan berarti token tidak viral. Log scanner dan pengiriman Telegram tetap berjalan.
