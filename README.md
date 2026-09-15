# TelefasilBot 🤖

Bot Telegram untuk fasilitator **Cakrawala University** — mencatat **Zoom Record**, **absen mahasiswa**, **rekap kehadiran fasil**, **backup**, dan **cancel kelas** langsung ke **Google Sheets**. Bukti rekap otomatis diunggah ke **Google Drive**.

**Bot live:** [@telefasil_bot](https://t.me/telefasil_bot)

> Dibuat oleh **Adzril Adzim** — [LinkedIn](https://linkedin.com/in/adzriladzim) · [Instagram](https://instagram.com/adzradzen07)

---

## 📑 Daftar Isi

- [1. Apa itu TelefasilBot?](#1-apa-itu-telefasilbot)
  - [Untuk siapa](#untuk-siapa)
  - [Arsitektur singkat](#arsitektur-singkat)
- [2. Mulai Cepat (untuk fasilitator)](#2-mulai-cepat-untuk-fasilitator)
- [3. Cara Pakai per Fitur](#3-cara-pakai-per-fitur)
  - [3.1 /start — menu utama](#31-start--menu-utama)
  - [3.2 /register — daftar nama fasilitator](#32-register--daftar-nama-fasilitator)
  - [3.3 /zoom — isi Zoom Record](#33-zoom--isi-zoom-record)
  - [3.4 /rekap — rekap kehadiran fasil](#34-rekap--rekap-kehadiran-fasil)
  - [3.5 /absen — rekap kehadiran mahasiswa](#35-absen--rekap-kehadiran-mahasiswa)
  - [3.6 /backup — izin & backup fasil](#36-backup--izin--backup-fasil)
  - [3.7 /cancel — lapor kelas cancel](#37-cancel--lapor-kelas-cancel)
  - [3.8 /schedule — jadwal minggu ini](#38-schedule--jadwal-minggu-ini)
  - [3.9 /cancel & /skip — batal dan lewati](#39-cancel--skip--batal-dan-lewati)
- [4. Notifikasi Otomatis](#4-notifikasi-otomatis)
- [5. Bukti Foto / Dokumen](#5-bukti-foto--dokumen)
- [6. Fitur Pintar (Auto-fill)](#6-fitur-pintar-auto-fill)
- [7. Pengaturan Admin](#7-pengaturan-admin)
  - [7.1 Prasyarat](#71-prasyarat)
  - [7.2 Bot Telegram via BotFather](#72-bot-telegram-via-botfather)
  - [7.3 Google Service Account](#73-google-service-account)
  - [7.4 Share Spreadsheet & Folder Drive](#74-share-spreadsheet--folder-drive)
  - [7.5 Daftar Lengkap Variabel .env](#75-daftar-lengkap-variabel-env)
  - [7.6 Jalankan Lokal (Windows)](#76-jalankan-lokal-windows)
  - [7.7 Jalankan Lokal (Linux/macOS)](#77-jalankan-lokal-linuxmacos)
  - [7.8 Deploy ke Railway](#78-deploy-ke-railway)
- [8. Troubleshooting](#8-troubleshooting)
- [9. Struktur Project](#9-struktur-project)
- [10. Catatan Teknis (untuk dev)](#10-catatan-teknis-untuk-dev)

---

## 1. Apa itu TelefasilBot?

TelefasilBot adalah **asisten pencatat harian fasilitator (fasil)** di Cakrawala University. Dulunya fasil harus buka Google Spreadsheet satu-satu, isi 14 kolom Zoom Record, hitung absen manual, upload bukti foto ke Drive, dan lapor backup/cancel lewat chat grup. Sekarang semua cukup dari satu bot Telegram:

- **Tulis Zoom Record** tanpa buka spreadsheet — pertemuan, skema, Zoom, dosen, jam, SKS **keisi otomatis** dari jadwal.
- **Absen mahasiswa** ke sheet Absen (15 prodi) dengan checklist nama, ketik NIM, atau satu status untuk banyak mahasiswa.
- **Rekap kehadiran fasil** — bukti foto otomatis terupload ke Drive + hitungan hadir/feedback diambil dari sheet otomatis.
- **Lapor backup** (izin + fasil pengganti) dan **cancel kelas** sekali klik.
- **Dijapri** tiap jadwal kelas lewat reminder otomatis.

### Untuk siapa

| Orang | Gunanya |
|-------|---------|
| **Fasilitator (pemakai utama)** | Mencatat semua kewajiban harian tanpa buka Google Spreadsheet |
| **Admin / koordinator** | Lihat stats pemakaian bot, mode darurat, atur Google Sheets & Drive |
| **Tim akademik** | Data Zoom Record, Absen, Rekap, Backup, Cancel langsung rapi di spreadsheet |

Bot ini **multi-user**: satu bot dipakai banyak fasilitator, dan setiap orang **hanya melihat jadwalnya sendiri** (nama terhubung ke chat lewat `/register`).

### Arsitektur singkat

```
Telegram (bot @telefasil_bot)
        │  polling updates (python-telegram-bot v22, JobQueue)
        ▼
handlers/*.py  ── conversation form (tombol + ketik manual)
        │
        ▼
sheets.py  ── gspread + Google Drive API (cache 5 mnt, write-through)
        │
        ├─► Google Sheets: Jadwal Fasil, Zoom Record, Backup, Cancel,
        │                  Absen (15 prodi), Rekap Kehadiran
        └─► Google Drive: folder Bukti per fasil (foto rekap)
```

Alurnya: **user → Telegram → bot (polling) → Google Sheets/Drive → balasan konfirmasi**. Tidak ada webhook/server sendiri; bot jalan sebagai proses (dengan JobQueue untuk reminder). Semua akses Google pakai satu Service Account.

---

## 2. Mulai Cepat (untuk fasilitator)

1. Buka [@telefasil_bot](https://t.me/telefasil_bot) di Telegram → tekan **Start** (atau kirim `/start`).
2. Daftar nama: ketik `/register <nama>` — boleh nama pendek, mis. `/register ratu` → bot cari di sheet Jadwal Fasil, kalau ketemu 1 nama langsung tersimpan, kalau lebih dari 1 akan muncul tombol pilihan.
3. Mulai catat kelas hari ini: kirim `/zoom` (atau tekan tombol **📝 Zoom** di menu).
4. Setelah kelas selesai: `/absen` lalu `/rekap` (jangan lupa bukti foto).

> Kapan pun bingung: kirim `/help` atau `/start`. Setiap form ada tombol **◀️ Kembali** untuk mundur satu langkah dan **❌ Batal** untuk membatalkan.

---

## 3. Cara Pakai per Fitur

### 3.1 `/start` — menu utama

Menyapa user + menampilkan tombol menu:

```
📝 Zoom    🧾 Rekap
✅ Absen   🔄 Backup
❌ Cancel  🗓 Jadwal
ℹ️ Help
```

- Kalau **belum terdaftar** → bot minta `/register <nama>` dulu.
- Kalau **sudah terdaftar** → bot sebut nama kamu dan langsung siap dipakai.

**Contoh:** kirim `/start` →

> 👋 Halo Riko!
>
> Terdaftar sebagai: Riko Julianto
>
> Aku bot pencatat keseharian fasil di Cakrawala University.
> Tekan tombol di bawah atau kirim /zoom untuk mencatat kelas hari ini.

Tombol menu sama saja dengan perintah — **📝 Zoom** = `/zoom`, **✅ Absen** = `/absen`, dst.

### 3.2 `/register` — daftar nama fasilitator

Mengikat chat Telegram kamu ke nama di sheet **Jadwal Fasil**. Wajib sekali sebelum pakai fitur lain.

**Cara pakai:**
```
/register <nama>
```

- Boleh **nama pendek** atau parsial, contoh: `/register ratu`, `/register adzr`.
- Bot **cocokkan ke nama lengkap** di sheet jadwal. Satu hasil → langsung tersimpan. Banyak hasil → muncul tombol pilihan, tap nama yang benar.
- Nama bisa **diganti** kapan saja dengan `/register` lagi.

**Contoh:**
```
/register ratu
```
> ⏳ Cari nama di Jadwal Fasil...
>
> 🔍 Ditemukan 2 nama cocok dengan "ratu":
> - Ratu Bilqis
> - Ratu Ayu
>
> Pilih yang benar:

Tap salah satu → `✅ Terdaftar sebagai: Ratu Bilqis`

> Kalau muncul `❌ "x" tidak ditemukan di Jadwal Fasil` berarti nama itu tidak ada di sheet jadwal (atau ketik salah). Coba nama yang lebih lengkap.

### 3.3 `/zoom` — isi Zoom Record

**Alias:** `/log`, `/zoom_record`. Mencatat kelas yang sudah diampu ke tab **Zoom Record** (14 kolom: tanggal pengisian, fasil, tanggal kelas, semester, kelas, mata kuliah, pertemuan, skema, SKS, tipe, dosen, jam mulai, Zoom, catatan).

**Alur step-by-step:**

| Step | Yang bot tanyakan | Cara jawab |
|------|-------------------|------------|
| 1️⃣ | Pilih kelas | Tombol daftar kelas kamu (✅ = sudah diisi untuk jadwal terakhir) |
| 2️⃣ | Pertemuan | Tombol `✅ Pakai N` (saran otomatis) / `🔄 Jadi N2` / `✏️ Ketik manual` (contoh: `3` atau `3 dan 4`) |
| 3️⃣ | Skema kelas | Tombol `🌐 Online` atau `🏫 Offline` |
| 4️⃣ | Zoom | **Otomatis** dari jadwal (`Zoom 33`), tak perlu ketik |
| 5️⃣ | Catatan fasil | Ketik teks atau tekan `⏭ Skip` |
| 6️⃣ | Konfirmasi | Tombol `✅ Submit` / `❌ Batal` / `◀️ Kembali` |

**Contoh alur lengkap:**

```
/zoom
```
> 1️⃣ Pilih kelas: (✅ = sudah isi untuk jadwal terakhir)
>
> - Glitch — Pengantar Ilmu Komputer (Senin 13.00 - 15.30)
> - 3Ilkom — Struktur Data (Selasa 08.00 - 10.30)
> - ❌ Batal

Tap `3Ilkom — Struktur Data` →

> 2️⃣ Pertemuan: **3** (auto pertemuan 3, terakhir 2)
> 3️⃣ Skema kelas:
>
> [🌐 Online] [🏫 Offline]

Tap `🌐 Online` →

> 4️⃣ Zoom: Zoom 33 (auto dari Jadwal)
> 5️⃣ Catatan fasil? Ketik teks atau tekan Skip.
>
> [⏭ Skip]

Ketik catatan (atau Skip) →

> 📋 **Konfirmasi data:**
> • Tanggal pengisian: 16/09/2026
> • Fasilitator: Riko Julianto
> • Tanggal kelas: 22/09/2026
> • Semester: 3 & 4
> • Mata kuliah: Struktur Data
> • Kelas: 3Ilkom
> • Pertemuan: 3
> • Skema: Online
> • SKS: 2
> • Tipe: Reguler
> • Dosen: Budi Santoso
> • Jam mulai: 08.00
> • Zoom: Zoom 33
> • Catatan: Materi graph
>
> Lanjut submit?
>
> [✅ Submit] [❌ Batal] [◀️ Kembali]

Tap **✅ Submit** →

> ✅ Tercatat di sheet! 3Ilkom — pertemuan 3 (Online).
> Kirim /log untuk entry berikutnya.

> **Catatan:** kalau Submit gagal sesaat, bot menawarkan tombol **✅ Retry** (maks 3x). Data baru dikirim ke sheet saat kamu menekan Submit — sebelum itu belum tersimpan.

### 3.4 `/rekap` — rekap kehadiran fasil

Mencatat kehadiran fasil per kelas ke spreadsheet **Rekap Kehadiran**, termasuk **upload bukti** ke Google Drive. Bot juga **menghitung otomatis** jumlah hadir dari sheet Absen + Feedback.

**Alur step-by-step:**

| Step | Yang bot tanyakan | Cara jawab |
|------|-------------------|------------|
| 1️⃣ | Pilih kelas | Tombol (✅ = lengkap, ⚠️ = baris rumpang yang bisa dilengkapi) |
| 2️⃣ | Pertemuan | Auto dari Zoom Record + tombol pilihan / ketik manual |
| 3️⃣ | Tipe kelas | Tombol `🌐 Online` / `🏫 On-site` |
| 4️⃣ | Sesi | Tombol `Kelas Biasa` / `Guest Lecture` / `Workshop/E-Lab` / `Lainnya` (atau ketik) |
| 5️⃣ | Peran | Tombol `Fasilitator Kelas` / `Moderator Guest Lecture` / `Backup Fasil` |
| 6️⃣ | Bukti | Kirim foto / dokumen gambar / paste link Drive / `/skip` |
| 7️⃣ | Konfirmasi | Tombol `✅ Submit` |

**Fitur keren:**

- **Deteksi baris rumpang** — kalau tanggal kelas ini sudah ada baris tapi belum lengkap (mis. bukti kosong), bot tawarkan `🧩 Lengkapi baris ini` supaya tidak dobel.
- **Auto-count** — total/hadir/feedback dihitung dari sheet Absen + Feedback; kalau absen belum ada, kolom jumlah dikosongkan dengan catatan.
- **Bukti lama bisa dilengkapi** tanpa upload ulang — cukup lanjut isi kolom yang kurang.

**Contoh:**

```
/rekap
```
> 1️⃣ Pilih kelas: (✅ = lengkap, ⚠️ = rumpang/lengkapi)
>
> - ⚠️ 3Ilkom — Struktur Data (Selasa 08.00 - 10.30)
> - ❌ Batal

Tap kelas → pilih pertemuan/tipe/sesi/peran → step bukti:

> 6️⃣ Bukti kehadiran? Kirim **foto** screenshot, dokumen **gambar** (drag-drop), paste **link Drive**, atau /skip.

Kirim foto →

> ⏳ Upload bukti ke Drive...
> ✅ Bukti terupload: 22 September 2026_Budi Santoso_Struktur Data_Riko Julianto.jpg

Lalu konfirmasi:

> 📋 **Konfirmasi Rekap:**
> • Tanggal: 22 September 2026
> • Dosen: Budi Santoso
> • Jam: 08.00 - 10.30
> • Kelas: 3Ilkom
> • Matkul: Struktur Data
> • SKS: 2
> • Pertemuan: 3
> • Tipe: Online
> • Sesi: Kelas Biasa
> • Peran: Fasilitator Kelas
> • Bukti: (link Drive)
> • Total/Hadir/Feedback: 45/42/40
>
> Submit?
>
> [✅ Submit] [❌ Batal] [◀️ Kembali]

### 3.5 `/absen` — rekap kehadiran mahasiswa

Mencatat kehadiran mahasiswa ke sheet **Absen** (bot mendeteksi blok kelas di **15 prodi sekaligus** — satu kode kelas bisa terletak di beberapa tab prodi, semuanya diisi).

**Kode status:**

| Tombol | Arti | Dihitung sebagai |
|--------|------|------------------|
| `S` | Hadir **Onsite** | Total + Hadir |
| `O` | Hadir **Online** | Total + Hadir |
| `A` | **Absent** (tanpa keterangan) | Total (tanpa hadir) |
| `I` | **Izin** | Tidak masuk total hadir |
| `SF` `OF` | Status formal/terpisah | Masuk total, dipisah dari hadir biasa |

**Alur step-by-step:**

| Step | Yang bot tanyakan | Cara jawab |
|------|-------------------|------------|
| 1️⃣ | Kode kelas | Tombol (⭐ kelas hari ini duluan, 🔄 = backup) atau `⌨️ Ketik Kode Lain` |
| 2️⃣ | Pertemuan ke-? | Angka **1–16** |
| 3️⃣ | Input via | Tombol `⌨️ Ketik NIM` / `☑️ Checklist Nama` / `📸 Upload Foto` |
| 4️⃣ | Input data | Ketik NIM pisah koma, atau tap nama di checklist, atau kirim foto |
| 5️⃣ | Status | Tombol S / O / A / I / SF / OF |
| 6️⃣ | Konfirmasi | `✅ Submit` |

**Contoh (mode Ketik NIM):**

```
/absen
```
> 1️⃣ Pilih Kode Kelas: (2 hari ini)
>
> — Hari Ini (Selasa) —
> ⭐ 3Ilkom (hari ini)
> — Kelas Lain Saya —
> ⭐ Arch1
> Årch2
> [⌨️ Ketik Kode Lain] [❌ Batal]

Tap `3Ilkom` → `2️⃣ Pertemuan ke-? (1-16)` → ketik `3` → pilih `⌨️ Ketik NIM` →

> Ketik NIM (pisahkan koma, contoh: 26111600029, 26111600004):

Ketik `26111600029, 26111600004, 26111600015` → pilih status `O` →

> 📋 **Konfirmasi Absen:**
> • Kode: 3Ilkom
> • Pertemuan: 3
> • Status: O
> • Jumlah: 3
> • NIM/Nama: 26111600029, 26111600004, 26111600015
>
> Submit?

Tap **✅ Submit** →

> ✅ Absen tercatat: 3Ilkom pertemuan 3 → 3 mahasiswa status O

**Mode Checklist Nama:** daftar mahasiswa muncul 25 per halaman, tap nama untuk centang (⬜ → ✅), `Next ›` / `‹ Prev` untuk pindah halaman, `➕ Tambah Manual` untuk nama di luar daftar, lalu `✅ Selesai`.

> **Catatan mode foto:** kirim foto daftar hadir → bot membalas "fitur OCR segera" dan memintamu mengetik NIM/nama manual. Kirim ulang sebagai teks.

### 3.6 `/backup` — izin & backup fasil

Melapor kalau kamu **izin** dan kelas diambil alih **fasil pengganti**.

**Alur step-by-step:**

| Step | Yang bot tanyakan | Cara jawab |
|------|-------------------|------------|
| 1️⃣ | Kelas yang dibackup | Tombol daftar kelas |
| 2️⃣ | Hari/Tanggal izin | Ketik, contoh: `Senin, 8 September 2026` |
| 3️⃣ | Fasil pengganti | Ketik nama (pendek ok → otomatis jadi nama lengkap, pilih tombol kalau banyak) |
| 4️⃣ | Catatan | Ketik atau `⏭ Skip` |
| 5️⃣ | Konfirmasi | `✅ Submit` |

**Contoh:**

> 2️⃣ Hari/Tanggal izin? (contoh: Senin, 8 September 2026)

Ketik `Senin, 8 September 2026` →

> 3️⃣ Fasil pengganti? (nama)

Ketik `ratu` →

> Pengganti: ratu → **Ratu Bilqis** (kalau ambigu muncul tombol pilih)

> ✅ Backup tercatat: 3Ilkom → Ratu Bilqis

### 3.7 `/cancel` — lapor kelas cancel

Melapor kelas yang **dibatalkan** (kolom B–I; sisanya diisi tim akademik).

**Alur step-by-step:**

| Step | Yang bot tanyakan | Cara jawab |
|------|-------------------|------------|
| 1️⃣ | Kelas yang cancel | Tombol daftar kelas |
| 2️⃣ | Jadwal awal | Ketik, contoh: `Selasa, 9 September 2026` |
| 3️⃣ | Sesi/pertemuan ke-? | Angka 1–99 |
| 4️⃣ | Konfirmasi | `✅ Submit` |

**Contoh:**

> ✅ Cancel tercatat: 3Ilkom sesi 5

### 3.8 `/schedule` — jadwal minggu ini

Menampilkan **seluruh jadwal kamu pekan ini** (pribadi + backup) dari sheet Jadwal Fasil, dengan penanda **← HARI INI**.

**Contoh:**

```
/schedule
```
> 🗓 **Jadwal Kelas Minggu Ini**
>
> **Senin** 13.00 - 15.30
>   Glitch — Pengantar Ilmu Komputer
>   🏫 R.203 | 👤 Dewi Lestari | Zoom 28
>
> **Selasa** 08.00 - 10.30 ← **HARI INI**
>   3Ilkom — Struktur Data
>   🏫 R.105 | 👤 Budi Santoso | Zoom 33

### 3.9 `/cancel` & `/skip` — batal dan lewati

- **`/cancel`** — batalkan seluruh form yang sedang berjalan. Semua form juga punya tombol **❌ Batal**.
- **`/skip`** — lewati step opsional (catatan, bukti, dst).
- Form **timeout 1 jam** — kalau kamu diam terlalu lama, form tertutup dan bot minta mulai ulang.

---

## 4. Notifikasi Otomatis

Bot punya 2 macam pesan otomatis, semuanya dalam **WIB (UTC+7)**:

### 4.1 Reminder kelas — 04:00 / 12:00 / 20:00 WIB

Tiga slot harian (bisa diubah via env `REMINDER_TIMES`, nilai **UTC**):

| Slot UTC | WIB | Isi pesan |
|----------|-----|-----------|
| `21:00` UTC | **04:00 WIB** | **Jadwal penuh** hari ini (semua kelas) |
| `05:00` UTC | **12:00 WIB** | Hanya kelas yang **belum di-log** di Zoom Record |
| `13:00` UTC | **20:00 WIB** | Hanya kelas yang **belum di-log** di Zoom Record |

Contoh isi reminder 04:00 WIB:

> ⏰ **Pengingat — ada 2 kelas hari ini (Selasa):**
>
> • **08.00 - 10.30** 3Ilkom — Struktur Data
>   🏫 R.105 | Zoom 33
>   Jangan lupa isi /log setelah kelas ya!

- Slot 12:00/20:00 **tidak spam**: kalau semua kelas sudah tercatat di Zoom Record, reminder **dilewati**.
- Reminder hanya dikirim ke chat yang sudah `/register`.

### 4.2 Heartbeat bot aktif — 05:00 WIB

Setiap hari **05:00 WIB** (22:00 UTC) bot broadcast ke semua user terdaftar sebagai tanda bot hidup:

> ✅ **TelefasilBot aktif** — sistem normal.
> Ketik /zoom /absen /backup /cancel /schedule.
> Reminder kelas tetap jam 04:00 WIB bila ada jadwal hari ini.

Kalau pagi-pagi tidak dapat pesan ini padahal biasanya dapat, berarti bot down — cek Railway Logs / proses lokal.

---

## 5. Bukti Foto / Dokumen

Di step bukti `/rekap`, ada 3 cara mengirim bukti:

| Cara | Yang dikirim | Keterangan |
|------|--------------|------------|
| **Foto** | Kirim foto (screenshot Zoom / daftar hadir) | Paling umum; otomatis di-compress ke Drive |
| **Dokumen gambar** | Drag-drop file `.jpg` / `.png` / `.webp` | MIME `image/*`; file bukan gambar **ditolak** dengan pesan jelas |
| **Link manual** | Paste link Google Drive | Kalau upload gagal atau bukti sudah di Drive |

### Format nama file di Drive

File bukti otomatis dinamai:

```
{tanggal}_{dosen}_{matkul}_{nama_fasil}.{ext}
```

Contoh: `22 September 2026_Budi Santoso_Struktur Data_Riko Julianto.jpg`

- Karakter ilegal nama file Drive (`/ \ : * ? " < > | _`) dibersihkan otomatis.
- File masuk ke **subfolder atas nama fasil** di dalam folder `REKAP_BUKTI_FOLDER_ID`. Kalau subfolder kamu sudah ada, dipakai; kalau belum, dibuat otomatis.
- Link bukti ditulis ke kolom Bukti di sheet Rekap sebagai **HYPERLINK** dengan nama file tampil.

---

## 6. Fitur Pintar (Auto-fill)

Supaya fasil tidak perlu ketik apa-apa, bot mengisi banyak kolom otomatis dari sheet Jadwal Fasil:

| Fitur | Detail |
|-------|--------|
| **Pertemuan auto** | Dari Zoom Record (nomor pertemuan terakhir + 1). Tetap bisa ketik manual |
| **Zoom `XX` auto** | Prioritas: kolom Keterangan → Nomor Zoom. Ketik `33` tetap bisa, dinormalisasi jadi `Zoom 33` |
| **Semester auto** | Dari kolom RomBel, mis. `3 Ilkom, 4 Ilkom` → `3 & 4` |
| **Tanggal kelas auto** | Dari hari kelas → jadwal berikutnya (mis. Selasa → tanggal Selasa terdekat) |
| **Ceklis ✅** | Kelas yang sudah diisi untuk jadwal terakhir ditandai ✅ di daftar `/zoom` dan `/rekap` |
| **Baris kosong pertama** | Bot menulis ke baris kosong pertama — tidak pernah menimpa data orang lain |
| **Auto-count rekap** | Total/Hadir/Feedback dihitung dari Absen + Feedback |
| **Mode darurat** | Admin bisa set semua kelas Reguler jadi Online lewat `/darurat` |

---

## 7. Pengaturan Admin

> Bagian ini untuk **admin/dev**. Fasilitator tidak perlu baca ini — langsung pakai bagian [3](#3-cara-pakai-per-fitur).

### 7.1 Prasyarat

- Akun Google (untuk Service Account + Drive).
- Akun [Railway](https://railway.app) (opsional, untuk deploy 24/7).
- Repo ini di clone/unduh.

### 7.2 Bot Telegram via BotFather

1. Buka [@BotFather](https://t.me/BotFather) → `/newbot` → ikuti instruksi → dapat **token**.
2. Token dipakai di env `TELEGRAM_BOT_TOKEN`.

### 7.3 Google Service Account

Bot mengakses Google Sheets dan Drive menggunakan **Service Account** (bukan akun pribadi).

1. Buka [Google Cloud Console](https://console.cloud.google.com) → buat **New Project** (atau pakai project lama).
2. **Enable API:** *Google Sheets API* dan *Google Drive API*.
3. **Create Service Account:** *APIs & Services → Credentials → Create Service Account* → beri nama → Create.
4. **Buat kunci JSON:** klik service account → *Keys → Add Key → Create new key → JSON* → otomatis terunduh.
5. Simpan file sebagai `secrets/service_account.json` (di lokal), atau gunakan isinya untuk Railway (lihat [7.5](#75-daftar-lengkap-variabel-env)).

### 7.4 Share Spreadsheet & Folder Drive

1. Buka **setiap** spreadsheet yang dipakai bot (Jadwal, Zoom Record, Backup, Cancel, Absen, Rekap).
2. Klik **Share → masukkan email service account** (format: `namasa@namaproject.iam.gserviceaccount.com`) → role **Editor**.
3. Lakukan hal sama untuk **folder bukti** di Drive (`REKAP_BUKTI_FOLDER_ID`) → share ke email SA sebagai **Editor** (supaya bisa buat subfolder + upload).

> Kalau lupa share salah satu sheet → bot balas error seperti `Sheet 'Zoom Record...' tidak ditemukan` atau `Google API error 403` — lihat [Troubleshooting](#8-troubleshooting).

### 7.5 Daftar Lengkap Variabel `.env`

Salin `.env.example` ke `.env` lalu isi. Tabel lengkap:

| Variabel | Wajib | Contoh | Keterangan |
|----------|:-----:|--------|------------|
| `TELEGRAM_BOT_TOKEN` | ✅ | `123456:ABC-DEF...` | Token bot dari [@BotFather](https://t.me/BotFather) |
| `GOOGLE_SHEET_ID` | ✅ | `1Y5wFTBj_04tkp...` | Spreadsheet utama (Jadwal + Zoom Record + Backup + Cancel). Default bawaan = Batch 5 Ganjil 26/27 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | ✅ (lokal) | `secrets\service_account.json` | Path file JSON service account. **Lokal saja**; Railway kosongkan ini |
| `GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT` | ✅ (Railway) | `{"type":"service_account",...}` | Isi file SA dalam **1 baris**. Railway: kosongkan yang path, isi yang ini |
| `FACILITATOR_NAME` | ⛔ opsional | `Adzril Adzim Hendrynov` | Fallback nama lama (single-user). Multi-user daftar lewat `/register`, tidak wajib |
| `MASTER_SHEET_NAME` | ✅ | `Jadwal Fasil Blok A Ganjil 26/27` | Tab jadwal master |
| `ZOOM_RECORD_SHEET_NAME` | ✅ | `Zoom Record Blok A Ganjil 26/27` | Tab tujuan `/zoom` |
| `BACKUP_SHEET_NAME` | ✅ | `Backup Fasilitator Blok A Ganjil 26/27` | Tab tujuan `/backup` |
| `CANCEL_SHEET_NAME` | ✅ | `Kelas Cancel & Pengganti Blok A Ganjil 26/27` | Tab tujuan `/cancel` |
| `ABSEN_SHEET_ID` | ✅ | `1QA2K2HKBNQzt9...` | Spreadsheet Absen (berisi 15 tab prodi) |
| `ABSEN_SHEET_NAME` | ✅ | `Computer Science` | Tab prodi default untuk deteksi blok absen |
| `REKAP_SHEET_ID` | ✅ | `1FUK-c1AzTscfX...` | Spreadsheet Rekap Kehadiran fasil |
| `REKAP_BUKTI_FOLDER_ID` | ⛔ opsional* | `1_onp...` | Folder Drive bukti (ID dari `drive.google.com/drive/folders/<ID>`). Wajib kalau mau upload bukti ke Drive |
| `SEMESTER` | ⛔ opsional | `1` | Default semester bila kolom RomBel kosong |
| `REMINDER_TIMES` | ⛔ opsional | `"21,5,13"` | Jam reminder dalam **UTC**, pisah koma. `21,5,13` = 04:00/12:00/20:00 WIB |
| `REMINDER_ENABLED` | ⛔ opsional | `true` | Matikan reminder (`false`) kalau tidak mau |
| `REMINDER_HOUR` / `REMINDER_MINUTE` | ⛔ legacy | `21` / `0` | Cara lama 1 slot (masih didukung; lebih baru pakai `REMINDER_TIMES`) |
| `HEARTBEAT_HOUR` | ⛔ opsional | `22` | Jam heartbeat UTC (22 = 05:00 WIB) |
| `HEARTBEAT_MINUTE` | ⛔ opsional | `0` | Menit heartbeat |
| `HEARTBEAT_ENABLED` | ⛔ opsional | `true` | Matikan notif bot aktif (`false`) |
| `DARURAT_ONLINE` | ⛔ opsional | `false` | `true` = semua kelas Reguler otomatis Online (alternative `/darurat on`) |

> \* `REKAP_BUKTI_FOLDER_ID` wajib kalau `/rekap` mau upload foto bukti ke Drive. Tanpa itu, `/rekap` tetap jalan tapi error di langkah upload bukti → pakai link manual.

### 7.6 Jalankan Lokal (Windows)

```powershell
cd telegram-sheets-bot
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env        # lalu isi (lihat tabel di atas)
# taruh service_account.json di folder secrets\
py bot.py
```

Tunggu log:

```
INFO ... Bot ready.
INFO ... Starting polling (N registered user(s), default facilitator=None)
```

Lalu test `/start` di Telegram.

> **Windows tip:** `python` di beberapa mesin adalah stub Microsoft Store — pakai `py` (launcher) seperti contoh di atas.
>
> **Auto-start saat login Windows:** file `start_bot.vbs` bisa dicopy ke folder Startup (`Win+R` → `shell:startup`) supaya bot jalan background tiap login.

### 7.7 Jalankan Lokal (Linux/macOS)

```bash
cd telegram-sheets-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # lalu isi
# taruh service_account.json di folder secrets/ (atau set path-nya di .env)
python bot.py
```

### 7.8 Deploy ke Railway

> ⚠️ **PENTING:** bot **polling** Telegram — hanya **satu proses** yang boleh jalan. Kalau bot jalan di Railway **dan** lokal **bersamaan**, muncul konflik `getUpdates` (bot mati-mati hidup). Pilih SATU tempat, atau matikan yang lain.

**Langkah deploy:**

1. **Push repo ke GitHub.** Pastikan `.env`, `secrets/`, dan `data/` tidak ikut ter-commit (sudah ada di `.gitignore`/`.dockerignore`).
2. **Buat project:** [railway.app](https://railway.app) → *New Project* → *Deploy from GitHub* → pilih repo ini.
3. **Build:** sudah dikonfigurasi di `railway.json` (builder `DOCKERFILE`) + `Dockerfile` (Python 3.12-slim, perintah `python bot.py`). Tidak perlu ubah apa-apa.
4. **Volume untuk data:** *New Volume* → mount path **`/app/data`**. Ini menyimpan `users.json`, `usage.json`, dll supaya tidak hilang tiap redeploy.
5. **Variables:** isi semua env (tabel [7.5](#75-daftar-lengkap-variabel-env)). Untuk kredensial Google:
   - `GOOGLE_SERVICE_ACCOUNT_JSON` → **kosongkan**.
   - `GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT` → paste **seluruh isi** `service_account.json` dalam satu baris (Railway menulisnya jadi `secrets/service_account.json` saat start).
6. **Deploy:** Railway build → cek tab **Logs** sampai `Bot ready`.
7. **Test:** buka bot di Telegram → `/start` → harus membalas.
8. **Update rutin:**
   - **Disarankan matikan auto-deploy** pada *Settings* service (kalau aktif, tiap commit langsung deploy — berisiko kalau commit belum siap).
   - Untuk naikkan versi: *Deploy* → **Deploy Latest Commit** secara manual.

**Biaya (per 2026):**
- Railway trial: **$5 / 30 hari** (sekali pakai, tanpa kartu kredit untuk trial).
- Setelah trial: Free `$1/bln` (kecil, kurang untuk uptime 24/7), Hobby `$5/bln`, atau pindah VPS (~`$2.50/bln`) kalau mau unlimited.

---

## 8. Troubleshooting

| Gejala / Pesan | Penyebab | Solusi |
|----------------|----------|--------|
| Bot mati-matian, log `Terminated by other getUpdates request` / **Conflict: terminated by other getUpdates request** | Ada **2 proses polling** (Railway + lokal, atau bot ter-*restart* ganda) | Matikan salah satu. Cek Railway *Settings* auto-deploy + jangan `py bot.py` bareng Railway. Setelah deploy, tunggu instance lama benar-benar mati |
| `Terjadi kesalahan internal` saat Submit | Jaringan/timeout sesaat | Tekan Submit lagi — `/log` dan `/rekap` punya tombol **Retry** (maks 3x) |
| `Gagal simpan (1/3): ...` lalu `Gagal 3x: ...` (data tidak tersimpan) | Google Sheets lambat / quota / error | Cek error detail di Railway/console Logs. Data memang **belum** tersimpan — mulai ulang `/log` |
| `Google Sheets timeout (60 detik) — coba lagi.` | Panggilan Sheets >60 detik | Coba lagi beberapa menit. Kalau sering, cek koneksi + API quota |
| `Google API error 429 — rate limit, coba lagi nanti.` | Lewati batas kuota Google API | Tunggu beberapa menit, lalu ulangi |
| `Google API error 403` | SA bukan Editor sheet/folder | Share ulang ke email SA sebagai **Editor** (lihat [7.4](#74-share-spreadsheet--folder-drive)) |
| `Sheet 'X' tidak ditemukan — cek nama tab / config.` | Nama tab berubah / salah env | Sesuaikan `MASTER_SHEET_NAME`, `ZOOM_RECORD_SHEET_NAME`, dll. Bot baca nama tab live tiap request |
| `Tab rekap untuk <nama> tidak ketemu — hubungi admin.` | Nama kamu tidak ada tab rekap | Minta admin menyiapkan tab rekap untuk nama kamu |
| `Upload gagal: Drive: 403 Forbidden` / `Drive: 404 Not Found` | SA tidak punya akses folder bukti / ID folder salah | Share folder `REKAP_BUKTI_FOLDER_ID` ke SA (Editor), cek ID folder |
| `Folder Bukti belum diset (REKAP_BUKTI_FOLDER_ID)` | Env folder bukti kosong | Isi `REKAP_BUKTI_FOLDER_ID`, atau pakai link Drive manual di step bukti |
| `Kode X tidak ditemukan di sheet Absen (cek 15 prodi).` | Kode kelas tidak ada di sheet Absen | Cek kode sudah benar; kalau pakai tombol, kode otomatis dari jadwal |
| `❌ Tak ketemu: ...` di hasil absen | NIM/nama tidak ketemu persis di sheet | Pakai NIM penuh |
| `⚠️ Ambigu (pakai NIM penuh): ...` | Nama kembar/parsial cocok beberapa mahasiswa | Ulangi dengan NIM yang unik |
| `⚠️ Hanya dokumen gambar yang diterima...` | File bukan gambar dikirim sebagai bukti | Kirim foto/dokumen `.jpg`/`.png`/`.webp` atau link Drive |
| Bot `connecting` terus di `/zoom` / `/schedule` | Internet/VPN / Google lambat | Restart WiFi, cek Railway Logs, jalankan ulang `py bot.py` |
| `Sheet protected cell` (error tulis) | Kolom diproteksi owner | Minta unprotect kolom, atau pastikan SA jadi **Editor** (bukan Viewer/Commenter) |
| Data dobel setelah retry | Tekan Submit 2x cepat saat timeout | Bot punya **per-chat lock** anti double-write; kalau terlanjur dobel, hapus baris duplikat manual — bot selalu tulis ke baris kosong pertama |
| Reminder/heartbeat tidak terkirim | Env mati (`REMINDER_ENABLED=false`), user belum `/register`, atau bot down | Cek Heartbeat 05:00 WIB + Railway Logs |
| `/zoom` kosong / "Tidak ada kelas" | Belum `/register`, atau nama tidak cocok di sheet Jadwal | Ketik `/register <nama>` sesuai nama di sheet jadwal |

---

## 9. Struktur Project

```
telegram-sheets-bot/
├── bot.py                 # entry point: setup PTB, JobQueue, polling
├── config.py              # baca .env → Config (reminder slots UTC, layout kolom)
├── sheets.py              # SEMUA akses Google Sheets + Drive upload (gspread)
├── users.py               # registry chat_id → nama (data/users.json, multi-user)
├── usage.py               # tracking pemakaian per menu (data/usage.json)
├── requirements.txt       # python-telegram-bot, gspread, google-api-client, dotenv
├── .env.example           # template env variables
├── Dockerfile             # build Railway (python:3.12-slim)
├── railway.json           # konfigurasi deploy Railway (builder + start command)
├── .dockerignore          # hal-hal yang tidak ikut image
├── start_bot.bat / .vbs   # auto-start bot saat login Windows
├── handlers/
│   ├── start.py          # /start /help + tombol menu
│   ├── register.py       # /register (nama pendek → nama lengkap, fuzzy)
│   ├── log.py            # /zoom /log /zoom_record (form 6 langkah)
│   ├── rekap.py          # /rekap (7 langkah + upload bukti Drive)
│   ├── absen.py          # /absen (checklist/NIM/foto, 15 prodi)
│   ├── backup.py         # /backup
│   ├── cancel.py         # /cancel
│   ├── schedule.py       # /schedule
│   ├── reminder.py       # reminder 04:00/12:00/20:00 WIB (per-user jobs)
│   ├── heartbeat.py      # notif bot aktif 05:00 WIB (broadcast)
│   ├── stats.py          # /stats admin
│   ├── darurat.py        # /darurat (hidden, admin)
│   └── status.py         # indikator loading/typing
├── data/                 # users.json, usage.json, aliases.json, rekap_folders.json,
│                         # darurat.json, chats.json, heartbeat.log (TIDAK di-commit)
├── secrets/              # service_account.json (TIDAK di-commit)
└── verify_*.py           # self-check/stub uji (tanpa kredensial)
```

> `data/` dan `secrets/` tidak ikut git — di lokal dibuat manual, di Railway hidup di volume `/app/data` (SA dikirim via env `GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT`).

---

## 10. Catatan Teknis (untuk dev)

- **Python 3.12+**, pakai `python-telegram-bot[job-queue]>=22.8`, `gspread`, `google-api-python-client`, `python-dotenv`.
- **Waktu = WIB** (`Asia/Jakarta`/UTC+7) untuk semua logika hari; reminder dikonfigurasi dalam UTC di env (`21,5,13` = 04:00/12:00/20:00 WIB).
- **Cache sheets:** `_rows_cache` TTL 5 menit + `_tabs_cache` 10 menit; semua tulis **write-through** (invalidate cache), jadi data bot selalu fresh; edit manual admin bisa telat ≤5 menit terlihat.
- **Lock `_run`:** semua I/O gspread/Drive dijalankan lewat executor dengan **timeout 60 detik** + single-flight lock + **per-chat lock** di handler konfirmasi (anti double-write saat `concurrent_updates=True`).
- **Multi-prodi absen:** `_locate_absen_block(kode)` mengembalikan **semua** blok yang cocok di 15 tab prodi; `_absen_counts`: P = S+O+SF+OF, Q = S+O, R = A, S_blm = SF+OF; kolom pertemuan = `3 + (pertemuan - 1)`.
- **Mode darurat:** toggle `/darurat on|off` tersimpan di `data/darurat.json` (Railway: volume). Env `DARURAT_ONLINE=true` = default ON.
- **Heartbeat:** broadcast single job (bukan per-chat) dengan stagger + retry 1x, log ke `data/heartbeat.log`.
- **Rekap:** nama file bukti `{tanggal}_{dosen}_{matkul}_{nama_fasil}.{ext}`, karakter ilegal di-sanitasi; subfolder per fasil di-cache di `data/rekap_folders.json`.
- **Self-check:** `verify_*.py` adalah stub test tanpa kredensial — jalankan `py -m compileall -q .` untuk cek sintaks, dan jalankan stub untuk regresi logika.
- **JANGAN** jalankan bot lokal **bersamaan** dengan Railway (konflik polling `getUpdates`).

---

Dibuat oleh **Adzril Adzim** — [LinkedIn](https://linkedin.com/in/adzriladzim) · [Instagram](https://instagram.com/adzradzen07)