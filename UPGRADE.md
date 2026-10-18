# Upgrade Solter 3.6.0 di Termux

1. Hentikan proses lama dengan `Ctrl+C`.
2. Cadangkan `.env` dan folder `data/`.
3. Ekstrak paket baru dan pindahkan `bot.py` serta seluruh modul di folder `solter/` ke instalasi Anda. Pertahankan `.env` dan folder `data/` yang sudah ada.
4. Jalankan:

```sh
cd ~/solter
python -m pip install -r requirements.txt
python bot.py doctor
python bot.py stats
python bot.py run
```

**Contoh jika ZIP tersimpan di `~/storage/downloads`:**

```sh
mkdir -p ~/solter-upgrade360
unzip -oq ~/storage/downloads/solter-v3.6.0-smart-decision.zip -d ~/solter-upgrade360
cp -f ~/solter-upgrade360/solter-v3.6.0/bot.py ~/solter/bot.py
cp -f ~/solter-upgrade360/solter-v3.6.0/solter/*.py ~/solter/solter/
```

Modul baru `decision.py` dan `outcomes.py` harus ikut tersalin. Database menambah dua tabel secara otomatis tanpa menghapus riwayat alert sebelumnya. Untuk menelusuri token, gunakan `python bot.py trace --mint ALAMAT_TOKEN`.

Jika proses gagal setelah upgrade, simpan log dan pulihkan source dari cadangan; jangan hapus database atau `.env` untuk mengatasi error startup.
