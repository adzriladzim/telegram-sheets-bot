# TelefasilBot 🤖

Bot Telegram untuk fasilitator **Cakrawala University** — mencatat Zoom Record, absen mahasiswa, rekap kehadiran, backup, dan cancel kelas langsung ke Google Sheets. Multi-user: satu bot dipakai banyak fasilitator, tiap orang cuma lihat jadwalnya sendiri.

**Bot live:** [@telefasil_bot](https://t.me/telefasil_bot)

---

## ✨ Fitur

| Fitur | Command | Keterangan |
|-------|---------|------------|
| Zoom Record | `/zoom` (`/log`) | Isi form Zoom Record — kelas, pertemuan auto, skema, Zoom auto, catatan |
| Rekap Kehadiran | `/rekap` | Rekap kehadiran fasil per kelas + upload bukti foto + hitungan absen/feedback |
| Absen Mahasiswa | `/absen` | Checklist/NIM/foto → tulis S/O/A/I/SF/OF ke sheet Absen (15 prodi) |
| Backup Fasil | `/backup` | Lapor izin + fasil pengganti (nama pendek auto → nama lengkap) |
| Cancel Kelas | `/cancel` | Lapor kelas cancel (kolom B–I, sisanya tim akademik) |
| Jadwal | `/schedule` | Jadwal minggu ini + penanda HARI INI |
| Register | `/register` | Daftar/ganti nama (boleh nama pendek, mis. `/register adzril`) |
| Stats (admin) | `/stats` | Siapa pakai menu apa, kapan terakhir |
| Darurat (hidden) | `/darurat` | Toggle semua Reguler jadi Online |

**Otomatisasi:**
- ✅ Pertemuan auto dari Zoom Record (ketik manual tetap bisa)
- ✅ Zoom `Zoom XX` auto dari Jadwal (prioritas: kolom Keterangan → Nomor Zoom)
- ✅ Semester auto dari RomBel (`3 Ilkom, 4 Ilkom` → `3 & 4`)
- ✅ Tanggal Kelas = jadwal berikutnya dari hari kelas
- ✅ Ceklis ✅ kelas yang sudah diisi (per tanggal)
- ✅ Reminder 04:00 WIB kalau ada kelas + heartbeat bot-aktif 05:00 WIB
- ✅ Tulis ke baris kosong pertama (tidak menimpa data orang)
- ✅ Loading/typing indicator + tombol ◀️ Kembali di semua step

---

## 🚀 Cara Pakai (untuk fasilitator)

1. Buka [@telefasil_bot](https://t.me/telefasil_bot) → **Start**
2. `/register <nama>` — boleh pendek (`/register ratu` → Ratu Bilqis)
3. `/zoom` → pilih kelas → skema → catatan → Submit
4. `/absen` → pilih kode → pertemuan → checklist nama → status → Submit
5. `/rekap` → pilih kelas → tipe/sesi/peran → foto bukti → Submit

---

## 🛠️ Setup Lokal (Windows)

```powershell
cd telegram-sheets-bot
py -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # lalu isi (lihat tabel di bawah)
# taruh service_account.json di secrets\
python bot.py
```

### 1. Google Service Account
1. https://console.cloud.google.com → New Project
2. Enable **Google Sheets API** + **Google Drive API**
3. APIs & Services → Credentials → Create Service Account → Keys → ADD KEY → JSON
4. Simpan sebagai `secrets/service_account.json`
5. **Share** semua spreadsheet + folder Bukti ke email service account (`...@....iam.gserviceaccount.com`) sebagai **Editor**

### 2. Isi `.env`

| Variabel | Contoh | Keterangan |
|----------|--------|------------|
| `TELEGRAM_BOT_TOKEN` | `123:ABC...` | Dari [@BotFather](https://t.me/BotFather) |
| `GOOGLE_SHEET_ID` | `1Y5w...` | Spreadsheet Jadwal + Zoom Record + Backup + Cancel |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | `secrets\service_account.json` | Path file SA (lokal) |
| `GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT` | `{...}` | Isi JSON 1 baris (Railway, tanpa upload file) |
| `MASTER_SHEET_NAME` | `Jadwal Fasil Blok A Ganjil 26/27` | Tab jadwal |
| `ZOOM_RECORD_SHEET_NAME` | `Zoom Record Blok A Ganjil 26/27` | Tab tujuan `/zoom` |
| `BACKUP_SHEET_NAME` / `CANCEL_SHEET_NAME` | ... | Tab tujuan `/backup` `/cancel` |
| `ABSEN_SHEET_ID` / `ABSEN_SHEET_NAME` | ... | Spreadsheet + tab Absen |
| `REKAP_SHEET_ID` | `1FUK...` | Spreadsheet Rekap Kehadiran |
| `REKAP_BUKTI_FOLDER_ID` | `1_onp...` | Folder Drive Bukti (ID dari link folders/...) |
| `SEMESTER` | `1` | Default bila RomBel kosong |
| `REMINDER_HOUR/MINUTE/ENABLED` | `21/0/true` | 21 UTC = 04:00 WIB |
| `HEARTBEAT_HOUR/MINUTE/ENABLED` | `22/0/true` | 22 UTC = 05:00 WIB |
| `DARURAT_ONLINE` | `false` | `true` = Reguler auto Online |

### 3. Auto-start saat login Windows
File `start_bot.vbs` sudah dicopy ke Startup folder → bot jalan background tiap login.

---

## ☁️ Deploy Railway (trial 30 hari gratis, tanpa kartu kredit)

1. Push repo ini ke GitHub (**jangan** include `.env`, `secrets/`, `data/`)
2. [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. **Variables** → isi semua dari tabel `.env` di atas. Untuk kredensial Google, paste seluruh isi
   `service_account.json` ke `GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT`
4. **Volumes** → New Volume, mount path `/app/data` (biar `users.json` tidak hilang tiap redeploy)
5. Deploy → cek Logs sampai `Bot ready` → test `/start` di Telegram

> Trial: $5 sekali pakai / 30 hari. Habis itu Free $1/bln (kurang untuk 24/7) — lanjut Hobby $5/bln
> atau pindah VPS ($2.50/bln) kalau butuh unlimited.

---

## 📁 Struktur Project

```
telegram-sheets-bot/
├── bot.py              # entry point (polling + JobQueue)
├── config.py           # baca .env → Config
├── sheets.py           # semua akses Google Sheets + Drive upload
├── users.py            # registry chat_id → nama (data/users.json)
├── usage.py            # tracking pemakaian per menu
├── handlers/
│   ├── start.py        # /start /help + tombol menu
│   ├── register.py     # /register (alias + fuzzy match)
│   ├── log.py          # /zoom /log
│   ├── rekap.py        # /rekap (+ upload bukti Drive)
│   ├── absen.py        # /absen (checklist/NIM/foto)
│   ├── backup.py       # /backup
│   ├── cancel.py       # /cancel
│   ├── schedule.py     # /schedule
│   ├── reminder.py     # reminder 04:00 WIB
│   ├── heartbeat.py    # notif aktif 05:00 WIB
│   ├── stats.py        # /stats admin
│   ├── darurat.py      # /darurat (hidden)
│   └── status.py       # indikator typing/loading
├── data/               # users.json, usage.json, aliases.json (tidak di-commit)
├── secrets/            # service_account.json (tidak di-commit)
├── Dockerfile / railway.json / .dockerignore
└── start_bot.bat / start_bot.vbs  # auto-start Windows
```

---

## 🔧 Troubleshooting

| Gejala | Penyebab → Solusi |
|--------|-------------------|
| `Terjadi kesalahan internal` saat Submit | Timeout sesaat → tekan Submit lagi (retry 3x) |
| Bot `connecting` terus | Internet/VPN → restart WiFi, `python bot.py` ulang |
| `Sheet tidak ditemukan` | Nama tab berubah → sesuaikan `.env`, bot baca live tiap request |
| `protected cell` | Kolom diproteksi owner → minta unprotect / tambah SA sebagai editor |
| `/zoom` kosong | Belum `/register`, atau nama tidak ada di Jadwal |
| Data dobel setelah retry | Hapus baris duplikat manual; bot tulis ke baris kosong pertama |

---

Dibuat oleh **Adzril Adzim** — [LinkedIn](https://linkedin.com/in/adzriladzim) · [Instagram](https://instagram.com/adzradzen07)
