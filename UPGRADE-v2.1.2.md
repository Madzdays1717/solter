# Solter v2.1.2 — seleksi konsisten dan pengiriman lebih terukur

Dibangun dari v2.1.1-quality, bukan cabang v2.0.3. Filter kualitas, deduplikasi, maksimum 4 radar/jam dan jarak default 10 menit tetap berlaku. Nilai lama .env tetap digunakan kecuali menjalankan skrip upgrade.

## Pasang

Hentikan bot (Ctrl+C), cadangkan .env dan data saat proses berhenti. Timpa file kode dengan isi folder solter-v2; pertahankan .env dan data.

```bash
cd ~/solter
python upgrade-v2.1.2.py
python bot.py run
```

Script mengaktifkan 50 slot, 3 evaluasi RPC, 2 batch radar bersamaan, pacing 5 request HTTP RPC/detik total dan 2/detik per metode, 300/menit dan plafon internal 200.000/hari. Backup .env dibuat; jangan commit backup. Ini plafon internal, bukan kuota yang dijanjikan provider. Jika endpoint memiliki batas lebih rendah, sesuaikan .env. Tidak ada API berbayar atau trading otomatis yang diaktifkan.

## Perubahan substantif

- Radar membandingkan pool dan basis valuasi yang sama. Pergantian pool atau MC menjadi FDV memulai baseline baru.
- Waktu sampel adalah waktu respons diterima, bukan waktu mulai seluruh putaran. Sampel pembanding maksimal 45 detik, interval minimal tetap RADAR_CONFIRM_SECONDS.
- HTTP gagal, data hilang atau kualitas gagal langsung menghapus kontinuitas, termasuk bila terjadi di antara dua sampel yang berjarak kurang dari 15 detik.
- Kenaikan MC harus didukung harga USD naik minimal 1% antar sampel. Likuiditas tidak boleh turun lebih dari 10%. Ini heuristik, bukan aturan profit yang sudah dibuktikan.
- Kandidat diperingkat dengan normalisasi lama interval. Selisih hitungan rolling 5m tidak disebut pembeli unik atau volume beli bersih; data snapshot tidak menyediakannya.
- Sebelum mengirim, ambil ulang kandidat terbaik: pool/basis harus sama, kualitas tetap lolos, harga tidak turun >1% atau melonjak >5% dari sampel pemeringkatan, likuiditas tidak jatuh. Maksimal 3 kandidat dicoba per putaran, maksimal satu percobaan pengiriman agar timeout tidak menyebabkan rentetan alert.
- Batas notifikasi tetap bekerja; kandidat yang tertahan tetap terlihat di shortlist, bukan dianggap tidak ditemukan.
- Maksimal sepertiga batch diberikan ke kandidat yang menunggu konfirmasi; sisanya mengitari token lain agar penemuan tidak sepenuhnya tersisih.
- Dua batch HTTP berjalan bersamaan tetapi tetap memakai limiter DexScreener bersama. 30 token adalah ukuran batch endpoint, bukan batas seluruh daftar.
- Bukti penjualan creator yang tersimpan dan penolakan risiko tertentu dari cache memblokir radar. Ketiadaan bukti tidak berarti aman.
- WebSocket membangunkan evaluator setelah transaksi masuk. Permintaan holder tetap dibatasi dan tidak memblokir radar DexScreener yang terpisah.
- Limiter RPC menghormati Retry-After, menunggu di luar lock, membatasi laju per metode, serta tetap menghitung retry dalam anggaran.

## Diagnosis

```bash
python bot.py shortlist
python bot.py report
```

Shortlist berisi maksimal 20 status terbaru (10 menit), skor pemeringkatan dan usia pembacaan lokal. Skor bukan peluang profit. Report menampilkan status delivery radar, alasan kandidat tertahan dan rata-rata durasi putaran radar. Data ini membedakan masalah sumber data, syarat seleksi, cooldown dan pengiriman.

## Cakupan dan batas kejujuran

Tidak mengklaim setara infrastruktur GMGN. Tetap snapshot DexScreener plus alur on-chain per-token; belum ada stream semua transaksi/program, pemulihan riwayat tanpa batas, atau integrasi GMGN berbayar. Token yang belum terindeks, di luar daftar penemuan atau slot live dapat terlewat. Waktu respons diterima bukan timestamp kesegaran server; snapshot yang tersimpan di cache provider masih mungkin terjadi.

Likuiditas pool dan rasio transaksi hanya saringan; tidak membuktikan buyer organik, keamanan token, atau profit setelah fee/slippage. Konfirmasi beberapa sampel mengorbankan sebagian kecepatan untuk mengurangi noise. Hasil harus dinilai dari rekaman live, bukan jumlah alert.

Sumber arsitektur/limit:
- https://docs.dexscreener.com/api/reference
- https://solana.com/docs/references/clusters
- https://docs.gmgn.ai/index/gmgn-agent-api
- https://pumpportal.fun/fees/

## Validasi

Pemeriksaan sintaks dilakukan. Sesuai permintaan sebelumnya untuk tidak menjalankan tes panjang/background, suite tes tidak dijalankan ulang; file tes lama belum seluruhnya mengikuti desain radar baru. Tidak ada benchmark live atau pengujian HP/RPC pengguna. Jangan menganggap hasil pengujian versi lama sebagai validasi versi ini.
