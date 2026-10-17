# Solter v2.1.1 — seleksi momentum, bukan spam

## Apa yang berubah

- **Tidak ada alert dari satu snapshot** DexScreener. Perlu dua pembacaan berkualitas minimal 15 detik terpisah, dengan pertumbuhan MC minimal 2%, sedikitnya 4 tambahan transaksi beli tercatat, volume 5m bertambah minimal $500, dan peningkatan jual tidak dominan.
- Syarat awal konservatif: MC/FDV $5k–$100k, volume 5m >=$4k, 25 buy/5m, rasio buy/sell >=1,8, perubahan harga 5m +6% sampai +35%, likuiditas diketahui >=$2,5k. Volume tidak boleh melebihi 12x likuiditas pool pada jendela 5 menit. **Angka ini heuristik awal, bukan hasil backtest.**
- Kandidat dibandingkan menurut kekuatan momentum, **hanya satu alert teratas per putaran**. Batas baru maksimal **4 radar/jam**, jarak minimal **10 menit**, sekali per token, persisten setelah restart.
- CALL mendalam perlu skor minimal 60 (grade B), meskipun `.env` lama masih menyimpan `MIN_SCORE=0`.
- Balasan milestone 2x/3x/5x/10x dimatikan secara default untuk mengurangi spam. Notifikasi risiko **dev menjual** tetap tersedia dengan `FOLLOWUP_ALERTS=true`.
- Semua pemeriksaan cepat ini tetap hanya kandidat momentum, *bukan audit holder/mint/dev*. Dengan filter lebih ketat, pump yang terlalu dini atau berumur detik dapat terlewat.

## Update Termux tanpa kehilangan data

1. **Hentikan semua proses Solter lama** (di tmux `Ctrl+C`). Jangan menjalankan versi lama dan baru bersamaan karena menyebabkan duplikasi pesan.
2. Cadangkan berkas `.env` dan `data/solter-v2.sqlite3` (lebih aman setelah proses sudah berhenti).
3. Ekstrak ZIP baru di folder tersendiri, lalu salin kembali `.env` dan database lama ke folder baru (jangan menimpa kredensial dengan `.env.example`).
4. **Tidak wajib mengganti `.env` lama**. Versi baru punya batas seleksi dan notifikasi terpisah yang berlaku walau konfigurasi v2.1.0 masih longgar. Untuk pengaturan baru, lihat `.env.example`.
5. Jalankan `python -m pip install -r requirements.txt`, `python -m unittest discover -s tests -q`, `python bot.py doctor`, kemudian `python bot.py run`.

## Jika masih terlalu banyak alert

```dotenv
RADAR_QUALITY_MAX_ALERTS_PER_HOUR=2
RADAR_ALERT_GAP_SECONDS=1200
CALL_MIN_QUALITY_SCORE=75
FOLLOWUP_MILESTONES=false
```

Ini prioritas grade A dan maksimal dua alert radar/jam. Tentu lebih sedikit alert **dapat** melewatkan kesempatan baik. Untuk menghentikan seluruh radar Telegram tanpa menghentikan alur CALL terverifikasi: `RADAR_ENABLED=false`.

## Batasan penting

- `txns.m5.buys` DexScreener adalah **jumlah transaksi beli**, bukan jumlah wallet unik atau bukti volume organik; wash trading dan manipulasi tetap mungkin.
- Token yang belum muncul di DexScreener, yang tidak memiliki data likuiditas, dan pump yang selesai kurang dari interval konfirmasi bisa tidak terdeteksi oleh jalur radar. Jalur pemantauan on-chain/CALL tidak dimatikan.
- Tidak ada pengujian live jaringan Solana atau Termux di paket ini. Unit test menggunakan data tiruan. Filter bukan bukti aman, anti-rugpull absolut, atau strategi profit pasti.
- Pastikan `.env` dan database tidak ikut dipush ke GitHub publik.
