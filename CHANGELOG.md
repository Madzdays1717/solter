# Riwayat perubahan

## 3.2.1

- Menghitung market cap dari harga dan suplai yang diketahui jika transaksi tidak memuat market cap.
- Menyimpan suplai token pada database agar tersedia setelah aplikasi dijalankan ulang.
- Mengambil harga SOL/USD dari endpoint data pasar jika pembaruan stream belum tersedia.
- Menampilkan market cap yang belum diketahui sebagai `Belum tersedia` dan mempertahankan satuan SOL saat kurs USD belum tersedia.
- Format USD dengan nilai sangat kecil tidak lagi dibulatkan menjadi nol.

## 3.2.0

- Mesin momentum dan risiko dari transaksi Pump.fun/PumpSwap.
- Pemeriksaan wallet berdasarkan perdagangan yang teramati.
- Penyimpanan state dan riwayat alert.
