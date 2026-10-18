# Solter 3.6.0

## Smart Decision Engine

- Menambahkan modul `decision.py` untuk mengevaluasi kekuatan pembeli terbaru, tekanan jual, penurunan harga dari puncak, dan konsentrasi pembelian yang teramati.
- Memisahkan keputusan `CALL`, `WATCH`, dan `REJECT` tanpa memperlambat penerimaan event transaksi.
- Mengulang keputusan sesudah pengambilan informasi pasar kandidat agar momentum yang sudah hilang tidak tetap dipanggil.
- Menambahkan `outcomes.py` yang menyimpan hasil pengamatan pasca-keputusan dari feed transaksi yang sudah berjalan, tanpa polling tambahan.
- Memperluas database dengan `decision_outcomes` dan `decision_history`. Perintah `stats` dan `trace` menampilkan hasil pengamatan serta alasan keputusan.
- Menjaga histori pengamatan maksimal 30 hari untuk mengendalikan ukuran penyimpanan.

Pengamatan harga yang tercatat bukan hasil perdagangan aktual. Kinerja live belum divalidasi sampai feed berhasil diakses dan alert Telegram teruji.
