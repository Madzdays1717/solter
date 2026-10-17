# Upgrade Solter v2.4.0 — MARKET (tanpa RPC Solana pada `run`)

Versi ini **mengubah jenis notifikasi**. Default `SCANNER_MODE=market` mengirim
**alert momentum yang BELUM diverifikasi on-chain**, bukan call aman/terverifikasi.
Ia hanya menemukan pool PumpSwap yang sudah migrasi, termasuk token lama yang
aktif kembali. Launch bonding-curve belum tercakup di jalur tanpa RPC ini.
API DEX/Gecko memakai snapshot/cache, jadi tidak ada jaminan latensi subdetik.

### Backup dan pemasangan (Termux)

1. Hentikan bot lama (`Ctrl+C`), jangan jalankan dua instance.
2. Taruh ZIP `solter-v2.4.0-market-only.zip` di folder Download Android.
3. Jalankan dari direktori Solter aktif (`~/solter` pada screenshot):

```sh
cd ~/solter
cp -p .env .env.backup
[ ! -d data ] || cp -a data data.backup
pkg install unzip -y
mkdir -p ~/solter-upgrade-240
unzip -oq ~/storage/downloads/solter-v2.4.0-market-only.zip -d ~/solter-upgrade-240
cp -f ~/solter-upgrade-240/solter-v2/solter/*.py ./solter/
cp -f ~/solter-upgrade-240/solter-v2/bot.py ./bot.py
cp -f ~/solter-upgrade-240/solter-v2/requirements.txt ./requirements.txt
python -m pip install -r requirements.txt
grep -q '^SCANNER_MODE=' .env && sed -i 's/^SCANNER_MODE=.*/SCANNER_MODE=market/' .env || printf '\nSCANNER_MODE=market\n' >> .env
python bot.py doctor
python bot.py run
```

Jangan salin `.env.example` menjadi `.env` saat upgrade: itu akan menimpa
Telegram token yang sudah bekerja. **Jangan menghapus `data/`**: SQLite lama
tetap dibaca, dan riwayat call lama tidak dihapus.

Jika folder proyek berbeda (`~/solter-v2`, dan sebagainya), sesuaikan `cd`.
Jalankan `termux-setup-storage` sekali bila `~/storage/downloads` belum ada.

### Pemeriksaan singkat

- `python bot.py doctor`: pastikan `scanner_mode: market`, `market_enabled: true`.
- `python bot.py probe`: mengecek GeckoTerminal dan DexScreener **tanpa RPC Solana**
  saat mode market aktif.
- `python bot.py stats`: cek `market_discovered`, `market_updated`,
  `market_signal_confirmed`, `market_alert_sent`, dan `no_call:market ...`.
- Di log cari `MARKET ALERT ... delivery=sent`. Angka `telegram_ms` hanya waktu
  mengirim pesan setelah keputusan, BUKAN umur snapshot atau latensi pasar.
- `rpc_today` bisa masih >0 dari sesi versi sebelumnya; itu metrik SQLite
  kumulatif pada hari UTC yang sama, bukan bukti mode market memakai RPC.
- Jika tidak ada alert: token harus punya MC, likuiditas, transaksi beli,
  volume, dan perubahan harga 5m sesuai ambang. Tidak ada jaminan alert setiap
  10/30 menit, dan tidak semua pasangan/pool bisa ditemukan via daftar gratis.

### Mengubah ambang tanpa menyentuh kode

```dotenv
SCANNER_MODE=market
MARKET_FAST_SECONDS=15
MARKET_SIGNAL_MIN_BUYS_5M=12
MARKET_SIGNAL_MIN_VOLUME_USD_5M=1500
MARKET_SIGNAL_MIN_CHANGE_PCT_5M=2
MARKET_SIGNAL_MAX_CHANGE_PCT_5M=85
MARKET_SIGNAL_BUY_SELL_RATIO=1.5
MIN_LIQUIDITY_USD=5000
MIN_MCAP_USD=3000
MAX_MCAP_USD=300000
MAX_CALLS_PER_HOUR=12
```

Jangan asal menurunkan semua ambang. Volume/transaksi dapat dipalsukan;
mode pasar **tidak memeriksa holder, mint/freeze, dev, atau wash trading**.
Jika ingin memakai pemindaian on-chain lama yang lebih terverifikasi dan
rela menunggu RPC, ubah `SCANNER_MODE=chain` lalu restart.

Untuk tes lokal (tidak memanggil API):

```sh
mkdir -p tests
cp -f ~/solter-upgrade-240/solter-v2/tests/*.py ./tests/
python -m unittest discover -s tests -q
```
