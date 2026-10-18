# Upgrade Solter 3.2

## Pemasangan aman

1. Hentikan bot yang sedang berjalan dengan `Ctrl+C`.
2. Backup `.env` dan database `data/solter-v2.sqlite3`.
3. Ekstrak paket ke folder baru agar file lama tidak ikut terbawa.
4. Pindahkan `.env` dan database dari folder lama ke folder baru.
5. Jalankan `python -m pip install -r requirements.txt`, `python bot.py doctor`, lalu `python bot.py run`.

Contoh jika ZIP ada di folder Download Termux:

```sh
cd ~
mkdir -p solter-next
unzip -oq ~/storage/downloads/solter-v3.2.0-independent.zip -d solter-next
cd ~/solter-next/solter-v3.2.0
cp ~/solter/.env .env
mkdir -p data
cp ~/solter/data/solter-v2.sqlite3 data/solter-v2.sqlite3
python -m pip install -r requirements.txt
python bot.py doctor
python bot.py run
```

Sesuaikan lokasi `~/solter` jika folder yang dipakai berbeda. Jika database belum ada, abaikan perintah menyalinnya. Solter membuat tabel yang diperlukan dan menambahkan kolom state baru secara otomatis, tanpa menghapus riwayat call yang lama.

Untuk mengganti isi repository GitHub, tempatkan isi folder `solter-v3.2.0` ke clone yang bersih dan review `git status` serta `git diff --cached` sebelum commit. Jangan commit `.env` atau database.
