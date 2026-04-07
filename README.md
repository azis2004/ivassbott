# Bot Telegram iVAS SMS OTP

Bot Telegram otomatis untuk ambil OTP dari iVAS SMS panel.

---

## Cara Setup (Mudah — pakai config.json)

### 1. Buat Bot Telegram
- Chat ke [@BotFather](https://t.me/BotFather)
- Ketik `/newbot`, ikuti instruksinya
- Salin **token** yang dikasih

### 2. Dapatkan Chat ID Kamu
- Chat ke [@userinfobot](https://t.me/userinfobot)
- Salin angka yang muncul

### 3. Isi config.json
Copy file `config.json.example` jadi `config.json`, lalu isi:

```json
{
  "BOT_TOKEN": "token_dari_botfather",
  "ADMIN_CHAT_ID": 123456789,
  "IVASMS_COOKIES": {"session": "isi_nanti_via_bot"},
  "POLL_INTERVAL": 30
}
```

> **Untuk cookies**: Bisa dikosongkan dulu (`{}`), nanti isi lewat command `/setcookies` di dalam bot.

### 4. Jalankan / Deploy

**Lokal:**
```bash
pip install -r requirements.txt
python main.py
```

**Railway:**
1. Push ke GitHub
2. Buka [railway.app](https://railway.app) → New Project dari GitHub
3. Tambahkan env vars: `BOT_TOKEN`, `ADMIN_CHAT_ID`, `POLL_INTERVAL`
4. Untuk cookies, pakai command `/setcookies` langsung di bot (lebih mudah)

---

## Cara Ambil Cookies iVAS SMS (dari HP)

1. Buka **Chrome** di HP
2. Login ke [ivasms.com](https://www.ivasms.com)
3. Di address bar ketik persis ini lalu tekan Enter:
   ```
   javascript:void(document.cookie)
   ```
4. Akan muncul teks panjang — itu cookies kamu
5. Copy teks itu
6. Kirim ke bot dengan format:
   ```
   /setcookies {"hasilnya": "paste disini"}
   ```

> **Alternatif mudah:** Kalau pakai PC, buka DevTools (F12) → Application → Cookies → copy semua sebagai JSON

---

## Perintah Bot

| Perintah | Fungsi |
|---|---|
| `/start` | Menu utama + status cookies |
| `/addnum` | Instruksi upload file nomor |
| `/setcookies <json>` | Update cookies iVAS SMS langsung dari Telegram |
| `/status` | Lihat total nomor + status cookies |

**Tombol:**
- **Ambil 5 Nomor** — Random pick 5 nomor dari daftar
- **Status Nomor** — Total nomor tersimpan
- **Hapus Semua Nomor** — Reset daftar (ada konfirmasi)

---

## Alur Pemakaian

1. `/start` → lihat status
2. `/setcookies {...}` → isi cookies iVAS SMS
3. `/addnum` → kirim file `.txt` berisi nomor
4. Tekan **"Ambil 5 Nomor"** → dapat 5 nomor
5. Daftarkan nomor ke WhatsApp
6. OTP masuk → bot otomatis forward ke kamu

---

## Format File .txt

Satu nomor per baris:
```
+62812345678
+62898765432
+1234567890
```

---

## Catatan

- Cookies iVAS bisa expired sewaktu-waktu. Kalau bot kirim notif "Login gagal", kirim `/setcookies` lagi dengan cookies baru.
- Cookies yang dikirim via `/setcookies` disimpan permanen di database lokal.
