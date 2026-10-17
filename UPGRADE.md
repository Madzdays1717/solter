# Upgrade bersih dari Solter v2.x (Termux)

**Jangan `git clean -fdx` atau hapus proyek seluruhnya:** langkah tersebut
berisiko menghapus `.env`, database, atau berkas lokal lain yang penting.

1. Hentikan bot v2 dengan `Ctrl+C`.
2. Lakukan backup dari folder lama `~/solter`:

```sh
cd ~/solter
mkdir -p "$HOME/solter-backup"
cp -p .env "$HOME/solter-backup/.env" 2>/dev/null || true
cp -r data "$HOME/solter-backup/data" 2>/dev/null || true
```

3. Ekstrak ZIP v3 ke **folder terpisah**, misalnya:

```sh
mkdir -p "$HOME/solter-new"
unzip "$HOME/storage/downloads/solter-v3.0.0-clean.zip" -d "$HOME/solter-new"
```

ZIP berisi direktori `solter-v3.0.0/`. Pilihan **teraman** adalah menjalankan
folder baru secara terpisah dan **tidak** menjalankan dua bot dengan token
Telegram sama:

```sh
cd "$HOME/solter-new/solter-v3.0.0"
cp "$HOME/solter-backup/.env" .env
mkdir -p data
cp "$HOME/solter-backup/data/solter-v2.sqlite3" data/ 2>/dev/null || true
python -m pip install -r requirements.txt
python bot.py doctor
python bot.py run
```

Setelah yakin folder baru yang ingin digunakan, Anda dapat mengganti folder
lama dengan folder baru (jangan hapus backup terlebih dahulu).

Jika proyek juga berada di GitHub, **jangan** langsung `git push` dari folder
baru yang tidak memiliki `.git`. Untuk membersihkan checkout Git yang ada,
gunakan salah satu branch baru, hapus modul legacy di checkout, lalu salin
berkas dari paket baru dan review `git status` sebelum commit.

**Catatan kompatibilitas:** v3 memakai tabel `candidates` dan `alerts` yang
berdampingan dengan tabel lama. Riwayat call v2 diimpor sebagai status `legacy`
untuk mencegah call ganda. Database pribadi tidak ikut paket ZIP.
