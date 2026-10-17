# Solter v2.4.1 — Telegram Visual & Banner

Hanya tampilan Telegram yang diubah. Mode market (tanpa RPC), syarat momentum,
riwayat database, dan algoritme discovery **tidak diubah**.

## Instalasi Termux dari v2.4.0

1. Hentikan bot (`Ctrl+C`). Jangan jalankan dua instance bot sekaligus.
2. Pastikan file ZIP sudah berada di `~/storage/downloads/` (bila perlu jalankan
   `termux-setup-storage` terlebih dahulu).
3. Jalankan di folder proyek aktif (contoh `~/solter`, sesuaikan jika berbeda):

```sh
cd ~/solter
cp -p .env .env.backup
[ ! -d data ] || cp -a data data.backup
pkg install unzip -y
mkdir -p ~/solter-upgrade-241
unzip -oq ~/storage/downloads/solter-v2.4.1-telegram.zip -d ~/solter-upgrade-241
cp -f ~/solter-upgrade-241/solter-v2/solter/*.py ./solter/
python bot.py doctor
python bot.py run
```

Penting: `*.py` disalin seluruhnya agar file **baru** `market_media.py` ikut dipasang.
Jangan timpa `.env` atau hapus folder `data/`. Jika Anda memakai v2.3 atau
lebih lama, lihat juga `UPGRADE-TERMUX.md` di ZIP untuk mode market v2.4.0.

## Format Telegram

- Judul dan angka momentum 5 menit dibuat mudah dibaca pada HP.
- Banner dari DexScreener token profile ditampilkan lewat pratinjau Telegram
  **jika metadata banner tersedia**; jika tidak tersedia, gunakan ikon token
  dari data pair/profile. Jika keduanya tidak ada, kirim teks biasa.
- Tidak ada HTTP ekstra untuk memuat gambar sebelum keputusan call. Telegram
  menampilkan pratinjau jika URL dapat diakses dan didukung Telegram;
  **pratinjau tidak dijamin tampil untuk semua hosting gambar**.
- Tautan X/Telegram/Website ditambahkan hanya bila tertera di metadata DEX.
  Tautan itu belum diverifikasi kebenarannya.
- Tombol: Chart (DexScreener), GMGN, Pump.fun, Solscan.
- Status keamanan **BELUM DIVERIFIKASI ON-CHAIN** tidak dihilangkan.

## Uji lokal (tanpa API)

```sh
mkdir -p tests
cp -f ~/solter-upgrade-241/solter-v2/tests/*.py ./tests/
python -m unittest discover -s tests -q
```
