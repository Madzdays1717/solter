# Solter v2.1.0 — Radar Volume & MC Aktif

## Mengapa sinyal terlewat di v2.0.2?

- Sweep pertama baru mulai pada sekitar 45 detik dan dipengaruhi batas RPC; sekarang target pemeriksaan pertama 10 detik **ditambah waktu menunggu giliran sweep**.
- Antrean fresh sebelumnya terbaru-dulu. Saat ramai, token lama berpotensi terus tersingkir; sekarang **FIFO** untuk token belum diperiksa, dan pengulangan token berdasarkan yang paling lama dicek.
- Hanya 16 token bisa masuk slot logsSubscribe, 3 penilaian mendalam paralel, dan CALL Telegram 5/jam; **radar terpisah dari semua slot itu**.
- Beberapa token Pump.fun mungkin belum muncul di DexScreener; jalur on-chain bonding curve dan WebSocket tetap digunakan sebagai cadangan, bukan dihapus.

## Radar baru (gratis)

- PumpPortal memberi stream peluncuran/migrasi (gratis), DexScreener mengambil batch hingga 30 token per HTTP request.
- Filter default: MC/FDV 3k–150k USD, volume rolling 5m >= $750, minimal 8 buy/5m, rasio buy/sell >=1.2, dan kenaikan harga 5m atau perubahan MC sejak sampel >=4%. Jika MC tersedia, dipakai; kalau hanya FDV, ditandai sebagai **estimasi**, bukan disebut MC aktual.
- MC tidak boleh merosot lebih dari 2% antar pembacaan apabila sudah ada sampel pembanding. Sinyal volume saja tidak cukup.
- Pemantauan mencakup token fresh/alive/watching/dead 30 menit terakhir. Token yang sudah diberi peringatan dicatat untuk mencegah spam ulang.
- Satu request per 30 alamat token, 120 alamat per putaran, jeda HTTP global 0,4 detik; ada penundaan adaptif saat HTTP 429.
- Batas radar independen 60 notifikasi/jam (ubah sesuai preferensi), **bukan** 5 CALL terverifikasi/jam.

**Penting:** Radar bukan hasil audit keamanan. Wash-trading dapat memalsukan volume dan tekanan buy. Token dengan MC naik tetap bisa rugpull. DexScreener dapat terlambat mengindeks atau tidak memiliki data token tertentu. Sistem ini tidak menjamin semua peluncuran/kenaikan tertangkap atau profit.

## Update pada Termux

1. Hentikan bot lama: `Ctrl+C` di tmux. Jangan jalankan dua proses.
2. Cadangkan `.env` dan `data/solter-v2.sqlite3` dari instalasi lama.
3. Ekstrak ZIP ke folder baru; salin `.env` dan database dari instalasi lama (jangan mengganti `.env` berisi kredensial dengan `.env.example`). Database lama akan di-upgrade otomatis saat start.
4. Di `.env` tambahkan pengaturan radar ini (atau gunakan default langsung):

```dotenv
RADAR_ENABLED=true
RADAR_INTERVAL_SECONDS=4
RADAR_BATCH_TOKENS=120
RADAR_TRACK_MINUTES=30
RADAR_MIN_MC_USD=3000
RADAR_MAX_MC_USD=150000
RADAR_MIN_VOLUME_5M_USD=750
RADAR_MIN_BUYS_5M=8
RADAR_MIN_BUY_SELL_RATIO=1.2
RADAR_MIN_MC_CHANGE_PCT=4
RADAR_MAX_ALERTS_PER_HOUR=60
DEX_SECONDS_BETWEEN=0.4
```

5. Jalankan:

```bash
python -m pip install -r requirements.txt
python bot.py doctor
python -m unittest discover -s tests -q
python bot.py run
```

## Metrik untuk diagnosis

`python bot.py stats` menampilkan `radar_alert_sent`, `radar_alert_rejected`, `radar_http_unavailable`, `radar_pair_not_listed`, dan `dex_429` di metrik. Jika `radar_pair_not_listed` tinggi, kemungkinan mayoritas token terlalu baru/belum diindeks; **itu bukan bukti tidak ada token bagus**. Jika `dex_429` bertambah, perbesar `DEX_SECONDS_BETWEEN` menjadi `0.8` atau `1` dan/atau kurangi `RADAR_BATCH_TOKENS`.

## Verifikasi

Pengujian otomatis mencakup volume, MC, rasio buy/sell, dedup, batching, fairness, dan migrasi database. Uji otomatis menggunakan respons tiruan, **bukan** bukti pemantauan live berhasil pada jaringan/Termux. Verifikasi real-time harus dilakukan di perangkat dengan Internet dan kredensial sendiri.
