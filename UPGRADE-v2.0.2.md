# Solter v2.0.2 — pemantauan dan ketahanan data

## Instalasi di Termux

1. Hentikan bot dengan Ctrl+C. Ekstrak ZIP, lalu salin isi folder `solter-v2` ke folder project `~/solter`, menimpa file kode. Jangan hapus `.env` atau folder `data`. ZIP tidak menyertakan keduanya.
2. Dari folder project:

```bash
cd ~/solter
python -m pip install -r requirements.txt
python tune-watch.py
python bot.py run
```

`tune-watch.py` membuat backup `.env` dan mengatur 16 slot, 3 evaluasi bersamaan, serta rotasi saat tidak ada transaksi selama 60 detik (setelah minimal 60 detik dipantau). Script tidak mengubah filter atau kredensial. Jangan unggah backup `.env` ke Git.

Jika melewatkan tune-watch.py, nilai MAX_ACTIVE_TOKENS dalam .env lama tetap berlaku. Nilai 16 adalah titik awal konfigurasi, bukan hasil benchmark Redmi Note 7. Gunakan 8 kembali jika koneksi kewalahan.

## Yang berubah

- Evaluasi independen, maksimal 3 tugas bersamaan, tanpa menunggu seluruh batch. Maksimal satu tugas per token; tugas dibatalkan saat shutdown.
- Persiapan pemeriksaan kualitas satu kali per sesi watch pada kandidat yang mulai aktif (minimal 4 buy, 3 buyer, net positif dan belum ada penjualan creator). Tidak memeriksa semua token sepi demi menjaga anggaran RPC.
- Kegagalan data sementara dapat diperiksa lagi setelah 10 detik, bukan cache penolakan 120 detik. Penolakan kualitas tetap 120 detik; pemeriksaan lolos tetap 45 detik.
- Momentum, creator, status pemantauan dan perubahan pasar diperiksa ulang setelah permintaan data selesai. Token yang sudah sepi/demoted tidak di-call memakai snapshot lama.
- Data holder kosong ditolak sebagai belum tersedia, bukan dihitung sebagai konsentrasi nol.
- Retry satu kali untuk HTTP 5xx; RPC error tertentu juga dapat dicoba ulang. Semua percobaan tetap dihitung dalam anggaran RPC.
- Cadangan HTTPS opsional melalui `SOLANA_RPC_FALLBACK_URL` di .env. Isi dengan endpoint yang kamu miliki; tidak ada endpoint/key tambahan yang disematkan. Cadangan berlaku untuk HTTP RPC, bukan WebSocket. Tidak melewati pembatasan penyedia.
- Log `WAIT` mencatat perubahan alasan tertahan; `CHECK duration=...` menunjukkan pemeriksaan yang memakan >=2 detik, termasuk menunggu jatah RPC.
- 16 slot default, batas dapat dikonfigurasi hingga 30. Kandidat bonding tetap diprioritaskan menurut laju inflow yang terukur; ini bukan pemeringkatan volume seluruh pasar.

## Pemeriksaan setelah berjalan

Buka sesi kedua, dari folder project:

```bash
python bot.py report
```

Perhatikan `holders unavailable`, `RPC ... error code=...`, `WAIT` dan durasi `CHECK`. Apabila data holder terus gagal, retry saja tidak cukup: perlu endpoint yang dapat melayani metode tersebut. Tidak ada jaminan call muncul atau token naik.

## Validasi

Diuji dengan suite lokal dan simulasi kegagalan holder, HTTP 503/fallback, evaluasi paralel, shutdown, serta momentum kedaluwarsa setelah RPC. Belum diuji live memakai endpoint, database dan perangkat Termux milik pengguna. Filter beli/jual dan keamanan tidak dilonggarkan. Prefetch dan lebih banyak slot dapat menambah konsumsi RPC; batas harian dan per menit tetap aktif.
