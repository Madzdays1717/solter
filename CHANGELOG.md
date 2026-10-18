# Catatan Rilis

## 3.5.0 — Social Observation

- Menambahkan `social.py` untuk pengamatan posting Bluesky publik yang menyebut CA token.
- Menghitung jumlah posting, akun unik, perubahan perhatian dalam interval waktu, dan kemiripan pola teks secara lokal.
- Menyimpan ringkasan sosial di SQLite dan menampilkannya melalui `trace --mint`.
- Menambahkan informasi sosial ke Telegram jika tersedia; pekerjaan jaringan sosial diproses terpisah dari pengiriman alert.
- Membatasi permintaan sosial secara otomatis dan menghormati pembatasan penyedia.
- Mempertahankan evaluator pasar dan pengamanan yang ada tanpa menjadikan analisis sosial sebagai syarat call.
