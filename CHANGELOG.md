# Riwayat perubahan

## 3.2.0

- Perhitungan konsentrasi pembelian, perilaku developer, aktivitas awal, dan transaksi multi-wallet dari event teramati.
- Parser trade membaca `breakdown` agar jumlah wallet dan nominal pembelian tidak dihitung secara keliru.
- Informasi risiko ditampilkan dengan definisi yang jelas dalam alert Telegram.
- State risiko awal dan identitas developer disimpan secara berkala dan dipulihkan setelah restart.
- Pemulihan metadata peluncuran dibatasi dan tidak menghambat jalur transaksi utama.
- Pendeteksian transaksi ganda memperhitungkan pool di dalam signature yang sama.
- Struktur modul tetap memisahkan stream, perhitungan, penyimpanan, dan pengiriman.
