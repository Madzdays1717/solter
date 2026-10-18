# Upgrade ke Solter 3.4.0

1. Hentikan bot di Termux dengan `Ctrl+C`.
2. Backup file `.env` dan seluruh folder `data/` dari proyek aktif.
3. Ekstrak ZIP ke folder sementara.
4. Salin **seluruh** file `.py` dari `solter-v3.4.0/solter/` ke folder `~/solter/solter/`. Ini termasuk modul baru `market.py`.
5. Ganti `bot.py` dan `requirements.txt` bila diperlukan. Jangan menimpa `.env` atau `data/`.
6. Jalankan:

```sh
cd ~/solter
python -m pip install -r requirements.txt
python bot.py doctor
python bot.py run
```

Jangan hanya menyalin `app.py` karena versi ini membutuhkan `market.py` dan perubahan `momentum.py` serta `telegram.py`.

**Validasi live:** perhatikan `SIGNAL` dan `ALERT event_to_delivery_ms` selama scanner berjalan. Alert tidak dijamin muncul dalam jangka waktu tertentu; feed pihak ketiga bisa mengalami masalah koneksi atau keterlambatan.
