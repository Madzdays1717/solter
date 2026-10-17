# Solter v2.2.1 → v2.2.2: upgrade aman di Termux

Perubahan ini **tidak menghapus atau memodifikasi histori database**. Jangan jalankan
dua proses bot pada database yang sama. Hentikan proses lama (`Ctrl+C`), simpan
backup `.env` dan folder `data/` sebelum mengganti file.

Jika sudah menjalankan v2.2.1, cukup ganti **dua file kode**:
`solter/engine.py` dan `solter/__init__.py`.

Contoh jika ZIP disimpan di folder Downloads Android dan repo di `~/solter-v2`:

```sh
pkg install unzip -y
termux-setup-storage  # jika belum pernah meminta izin penyimpanan
mkdir -p ~/solter-upgrade
unzip -oq ~/storage/downloads/solter-v2.2.2-holderfix.zip -d ~/solter-upgrade
cd ~/solter-v2
cp solter/engine.py solter/engine.py.backup
cp ~/solter-upgrade/solter-v2/solter/engine.py solter/engine.py
cp ~/solter-upgrade/solter-v2/solter/__init__.py solter/__init__.py
python bot.py doctor
python bot.py run
```

Jika masih menggunakan v2.1.0 atau v2.2.0, **jangan hanya mengganti dua file**.
Salin semua file kode dan requirements dari ZIP baru tanpa menimpa `.env`, `data/`,
atau folder `.git/` proyek Anda. Pasang persyaratan jika perlu:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -q
python bot.py doctor
python bot.py run
```

Periksa parameter `call_mode`, `max_watch`, `min_net_sol`, `min_score` pada hasil
`doctor`, karena `.env` lama dapat mengalahkan preset baru. Jika ingin preset
momentum-awal, set `CALL_MODE=cepat` dan hapus override filter lama yang tidak
disengaja (jangan hapus token/chat ID Telegram).

Untuk diagnosis setelah bot berjalan, bandingkan hasil `python bot.py stats`
sekarang dan lima menit kemudian. Lihat apakah `flow_confirmed`,
`holders_rpc_unavailable`, `quality_confirmed` dan `call_sent` bertambah.
`probe` menguji beberapa transaksi contoh saja; bukan jaminan streaming lengkap.

Tidak ada jaminan menghasilkan call, mendeteksi seluruh token, atau profit.
