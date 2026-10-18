# Pembaruan Solter 3.2.1

Hentikan aplikasi dan salin `.env` serta `data/` sebagai cadangan. Jangan menghapus database karena berisi riwayat alert.

Jika paket ada di folder Downloads Termux, dari proyek saat ini jalankan:

```sh
cd ~/solter
cp .env .env.backup
cp -a data data.backup
mkdir -p ~/solter-update
unzip -oq ~/storage/downloads/solter-v3.2.1-mcfix.zip -d ~/solter-update
cp -f ~/solter-update/solter-v3.2.1/solter/*.py ./solter/
python bot.py doctor
python bot.py run
```

Perintah tersebut memperbarui modul Python saja. `bot.py`, `.env`, dan database lama tidak ditimpa. Jika proyek bukan versi 3.2.0, ikuti cara migrasi source lengkap pada README lebih dulu. Setelah startup, database menambahkan kolom `supply` secara otomatis.

Saat feed tidak memberikan market cap dan jumlah suplai belum diketahui, alert menampilkan `Belum tersedia`. Saat feed harga SOL/USD belum siap, nilai ditampilkan dalam SOL. Aplikasi tetap mengirim alert tanpa menunggu harga tambahan.
