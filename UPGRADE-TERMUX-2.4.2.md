# Solter v2.4.2 — Telegram Milestone Follow-ups

Update hanya tampilan milestone Telegram dan peringatan dev/creator.
Mode market, filter dan discovery tidak berubah. Riwayat database tetap aman.

## Upgrade dari v2.4.1 di Termux

1. Hentikan bot dengan `Ctrl+C`. Jangan jalankan dua instance bersamaan.
2. Simpan ZIP ke folder Downloads pada Android.
3. Dari folder proyek aktif (sesuaikan bila bukan `~/solter`):

```sh
cd ~/solter
cp -p .env .env.backup
[ ! -d data ] || cp -a data data.backup
pkg install unzip -y
mkdir -p ~/solter-upgrade-242
unzip -oq ~/storage/downloads/solter-v2.4.2-milestones.zip -d ~/solter-upgrade-242
cp -f ~/solter-upgrade-242/solter-v2/solter/*.py ./solter/
python bot.py doctor
python bot.py run
```

Tidak perlu menyalin `.env.example` ke `.env`, dan jangan hapus database.

## Tampilan baru

- Balasan milestone 2×, 3×, 5×, 10×, 20×, 50×, 100× menampilkan
  harga referensi saat alert, puncak terpantau, persentase kenaikan harga,
  dan waktu puncak.
- Peristiwa loncat level (misalnya 1× -> 5×) hanya mengirim milestone 5×.
- Pesan milestone membalas alert awal serta menyertakan tombol Chart, GMGN,
  Pump.fun, Solscan. Banner tetap pada alert awal, tidak diunggah berulang.
- Mode market tetap mengingatkan bahwa keamanan belum diperiksa on-chain.
- Data DEX merupakan snapshot; kejadian di antara pembacaan dapat terlewat.

## Uji lokal (opsional)

```sh
cp -f ~/solter-upgrade-242/solter-v2/tests/*.py ./tests/
python -m unittest discover -s tests -q
```
