# Upgrade Solter v2.5.0 — perombakan Market Scanner (Termux)

Tidak perlu membuat akun PumpDev atau membeli RPC. **Jangan menjalankan bot
lama dan baru bersamaan** karena keduanya dapat mengirim alert ke Telegram.

## 1. Backup

Hentikan bot (`Ctrl+C`) lalu jalankan:

```sh
cd ~/solter
cp -p .env .env.backup
[ ! -d data ] || cp -a data "data.backup.$(date +%Y%m%d-%H%M%S)"
```

Sesuaikan `~/solter` jika proyek Anda berada di folder lain.

## 2. Upgrade semua modul (jangan hanya salin engine.py)

Unduh `solter-v2.5.0-fullfree.zip` ke Android Download, lalu:

```sh
cd ~/solter
pkg install unzip -y
mkdir -p ~/solter-upgrade-250
unzip -oq ~/storage/downloads/solter-v2.5.0-fullfree.zip -d ~/solter-upgrade-250
cp -f ~/solter-upgrade-250/solter-v2/solter/*.py ./solter/
cp -f ~/solter-upgrade-250/solter-v2/bot.py ./bot.py
cp -f ~/solter-upgrade-250/solter-v2/requirements.txt ./requirements.txt
python -m pip install -r requirements.txt
```

**Jangan menyalin `.env.example` ke `.env`** dan jangan menghapus folder
`data/`. SQLite menambah tabel `market_queue` otomatis pada startup; histori
call dan milestone lama tetap dipertahankan.

## 3. Jalankan

Pastikan `.env` menggunakan `SCANNER_MODE=market`. Jika perlu:

```sh
cd ~/solter
grep -q '^SCANNER_MODE=' .env && sed -i 's/^SCANNER_MODE=.*/SCANNER_MODE=market/' .env || printf '\nSCANNER_MODE=market\n' >> .env
python bot.py doctor
python bot.py run
```

Di sesi Termux lain:

```sh
cd ~/solter
python bot.py stats
python bot.py trace --mint 3NxfWMHmfQg8NJFW5AG1ti2xACdczWz24e9SKyk1pump
```

`trace` tidak bisa mengetahui MC saat token pertama ditemukan di versi lama;
`first_mcap_usd` baru terisi ketika v2.5 menerima snapshot valid pertamanya.
Jika `.env` mengandung `MAX_MCAP_USD=300000`, dalam mode market sekarang
berarti ambang **breakout**, bukan hard-stop. `MARKET_FAST_SECONDS` adalah
target refresh token panas, bukan jaminan feed per detik.

## 4. Validasi lokal (opsional)

```sh
cd ~/solter
mkdir -p tests
cp -f ~/solter-upgrade-250/solter-v2/tests/*.py ./tests/
python -m unittest discover -s tests -q
```

Hasil lulus offline tidak membuktikan kemampuan menangkap token live, latensi
provider, atau profit. Sinyal market tidak melakukan verifikasi holder/dev/
authority on-chain. Pastikan memeriksa token secara independen sebelum trading.
