# MEMORY — telegram-sheets-bot (TelefasilBot)

> Per-project memory. Read at cold session start. Append-only.
> Updated: 2026-09-15

## What
Bot Telegram fasilitator **Cakrawala University** → catat Zoom Record, absen, rekap kehadiran, backup, cancel kelas langsung ke Google Sheets. Multi-user (satu bot, tiap fasil lihat jadwal sendiri). Bot: [@telefasil_bot](https://t.me/telefasil_bot).

## Stack
- Python 3.12 + `python-telegram-bot` (polling + JobQueue) + `gspread`/Google API.
- Entry `bot.py`. Config `config.py` (baca `.env`). Sheets: `sheets.py`. User: `users.py` → `data/users.json`. Usage: `usage.py`.
- Handlers `handlers/`: start, register, log (/zoom), rekap, absen, backup, cancel, schedule, reminder (04:00 WIB), heartbeat (05:00 WIB), stats (admin), darurat (hidden), status.

## Run / Build
- Lokal Windows: `py -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt; copy .env.example .env; python bot.py`.
- **`python` cmd = MS Store stub (gagal). Pakai `py` launcher** (berlaku semua script Python di machine ini).
- Service account: `secrets/service_account.json` (lokal). Share semua spreadsheet + folder Bukti ke email SA sebagai Editor.
- Auto-start Windows: `start_bot.vbs` di Startup folder.

## Deploy (Railway)
- Builder DOCKERFILE (`railway.json`), startCommand `python bot.py`, restartPolicy ON_FAILURE max 10.
- Dockerfile: `python:3.12-slim`, `WORKDIR /app`, `CMD ["python","bot.py"]`. 17 baris.
- Kredensial Google di Railway = env `GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT` (paste JSON 1 baris), bukan upload file.
- Volume: mount Railway `/app/data` supaya `users.json`/`usage.json` persist antar redeploy.
- Trial $5/30 hari; Free $1/bln kurang 24/7; Hobby $5/bln atau VPS $2.50/bln.

## Decisions
- **2026-09-11 — Railway tolak instruksi `VOLUME` di Dockerfile.** Build `0bd7ddb1` gagal di Dockerfile L15. Fix `a50bf50`: buang `VOLUME`, persist lewat Railway Volume mount `/app/data`. Volume selalu mount via platform/dashboard, JANGAN Dockerfile.
- **2026-09-11 — Deploy Railway LIVE, single instance.** Deploy `eedeb629` ACTIVE. 5 env vars set, Volume attached `/app/data`. **Arsitektur final: HANYA bot Railway berjalan — lokal dimatikan total.** Alasan: dua instance polling = bentrok `getUpdates` (Telegram 409 Conflict).

## Gotchas
- `VOLUME` instruction → Railway build fail. Pakai platform volume.
- `.dockerignore` exclude `.env`, `secrets/`, `data/`, `*.log`, `.git/` — credentials/data tak masuk image. Verifikasi tiap ubah Dockerfile.
- Data dobel saat retry submit: bot tulis ke baris kosong pertama; hapus duplikat manual.
- **JANGAN jalankan bot lokal sambil Railway aktif** → dua poller → Telegram 409 Conflict, pesan acak hilang.

## Localhost Cleanup (2026-09-11)
- Local bot PID 16620 killed (`Stop-Process`).
- `TelefasilBot.vbs` (Startup dir) → di-rename `.disabled`.
- Startup folder bersih; Task Scheduler + Registry Run key bersih.
- Efek: Railway = satu-satunya instance polling.

## Status / Next
- `origin/main` = `eedeb629` (deploy ACTIVE, 5 vars, volume `/app/data`). Single instance Railway.
- NEXT: (1) verifikasi `/status` bot merespons + `/start` di @telefasil_bot; (2) pastikan `users.json`/`usage.json` persist antar-redeploy via volume. Selesai kalau kedua lolos.

## [2026-09-14] Root cause no-response + fix sync gspread (mode SAVE append)
- **ROOT CAUSE telegram no-response:** sync gspread BLOCK PTB loop — `rekap.py` (2 sheets: 2 spreadsheet) + `stats.py` (1) jalan sinkron di update handler. PTB `concurrent_updates=False` → serial; handler block >5s → PTB timeout → bot tak merespons. Bukan deploy/volume.
- **Fix commit `b978525`** (push `a50bf50..b978525`, working tree bersih). Railway redeploy triggered.
- **Review APPROVED — 0 issue (S1-S3).**
- **NEXT (user):** cek Railway ACTIVE + tes `/stats` + `/help` bareng di @telefasil_bot.
- **Konvensi:** sync external I/O di handler PTB = anti-pattern. Rekap/stats gspread jangan sekali lagi di update handler.

## [2026-09-14] Tahap1 $0 — concurrent scaleup (SHIPPED 2dadfa5)
- **SHIPPED:** commit `2dadfa5` pushed `b978525..2dadfa5`. Railway auto-deploy. Review APPROVED — 0 S1/S2.
- **NEXT (user):** cek Railway ACTIVE + tes 2 chat bareng + double-tap Submit di @telefasil_bot.
- **bot.py:** `.concurrent_updates(False)` → `True`. Per-chat `asyncio.Lock` via `sheets.SheetsClient.for_chat(chat_id)` dipakai di 5 confirm_cb (log, rekap, absen, backup, cancel) → same-chat gspread-write serialized, double-tap Submit tidak dobel-write. ConversationHandler tetap jalan (state per-chat, lock cuma di write).
- **sheets.py:** cache in-memory `_rows_cache[(ss_id, title)]` TTL 300s + `_tabs_cache` (10m) + `_cached_rows/_invalidate_rows`. Semua `get_all_values` hot path (master, zoom_record, backup, cancel, rekap tab, absen 15 prodi) lewat cache. Write-through: tiap sukses tulis → `_invalidate_rows(...)` (read pasca-tulis selalu fresh). Batch path: `values_batch_update` (append_record 10 cell → 1 call; update_absen; update_rekap_cells) + `values_batch_get` (absen all-prodi 1 call). Mutation cache aman — semua di bawah `_run` single-flight lock existing, TANPA lock baru.
- **Seed/flush:** `warmup()` (master+zoom+backup) di `post_init` + JobQueue `sheets:warm` `run_repeating(1800s)` — bot.py. Single instance Railway, volume `/app/data`, reminder/heartbeat jobs TIDAK disentuh.
- Verify: `py -m compileall` OK, import OK, grep handler tanpa gspread direct sync (hanya komentar), offline test `verify_sheets.py` PASS (TTL+invalidate, tabs cache, for_chat serialisasi same-chat + overlap cross-chat, nested lock no-deadlock). Live 2-user belum dites (butuh creds + write nyata) — reasoning ada di laporan.
- **Peringatan:** global `_run` lock tetap serialize SEMUA gspread I/O (read pun) — event loop nggak pernah block, tapi paralelisme sheets terbatas; cache yang motong jumlah call adalah win utamanya. TTL cache = data sheet bisa telat ≤5 menit untuk edit eksternal (admin), tulis bot sendiri selalu fresh (write-through).

## [2026-09-15] P0+P1+S1 SHIPPED e5233a6 (2dadfa5..e5233a6)
- **SHIPPED:** commit `e5233a6` pushed `2dadfa5..e5233a6`. Railway auto-deploy. Review **APPROVED**.
- **Isi (P0+P1+S1):**
  - **Secrets ignore** — `.env`, `secrets/`, data sensitif di-exclude dari version control (cek `.gitignore`/`.dockerignore`).
  - **students TTL300** — cache data students TTL 300s (konsisten pola TTL sheets).
  - **_run timeout60 + sanitasi** — global `_run` lock single-flight diberi timeout 60s + sanitasi (cegah hang/block permanent).
  - **darurat auth** — handler darurat (hidden) diberi autentikasi.
  - **usage lock** — akses data usage di-lock/serialized (hindari race write `usage.json`).
  - **except Exception** — catch broad di path kritis (fail-open, bot tetap hidup).
  - **env placeholders** — `.env.example` placeholder utk semua var env.
- **NEXT (user):** cek Railway ACTIVE + tes live di @telefasil_bot (verifikasi fitur P0/P1/S1 — sesi lanjut detail kode kalau perlu).

## [2026-09-15] ABSEN multi-blok + Mode C + backup picker + schedule backup + usage detail — 3bc1549
> Commit `3bc1549` pushed `e5233a6..3bc1549`. HEAD = 3bc1549. Railway auto-deploy. Working tree via commit message: "fix: sheets multi-blok absen (multi-prodi) + Mode C; absen backup picker + mode checklist + usage; schedule backup; usage extra detail".

- **Multi-blok absen AGREGAT (multi-prodi, sheets.py):**
  - `_locate_absen_block(kode)` → return SEMUA blok absen yang match Kode di 15 sheet prodi (JANGAN stop di match pertama). Tanpa match → `SheetsError "Kode {kode} tidak ditemukan di sheet Absen (cek 15 prodi)."`.
  - `_list_students` — roster (nim, nama, mode) dedup (nim,nama) lintas blok, cache `_absen_students_cache` TTL300 + `_invalidate_absen_students` (clear saat absen write); empty result cuma TTL, tak di-cache permanen.
  - `_absen_counts` — P=S+O+SF+OF, Q=S+O, R=A, S_blm=SF+OF; kolom pertemuan = 3+(pertemuan-1).
  - `_resolve_absen` — NIM exact-penuh, Nama contains; {blocks, matched, ambiguous, unmatched}.
  - `_update_absen` — write ke SEMUA blok via SATU `values_batch_update` (USER_ENTERED), invalidate rows tiap blok + roster.
- **Mode C info-only (soft-check DIBUANG 2026-09-15):** col-C = "Mode Kelas Asal". `_mode_incompatible` DIHAPUS + UI warning "⚠️ Mode tak cocok" dihapus. Alasan: true positive palsu — hybrid tercatat OFFLINE tiba-tiba ikut online (status O) kena warning. Kolom C teks bebas, tak bisa bedakan murni vs hybrid → pilih opsi (a). Mode cuma info: checklist nama tampil label `[mode]` (absen.py `_build_checklist_kb`), absen konfirm tak tampil warning mode.
- **Abseb backup picker:** picker kode kelas pisah personal (⭐) vs backup (🔄) via `get_all_loggable_classes` (personal+backup) + `backup_kodes`. Checklist nama tampil label `[mode]` (absen.py `_build_checklist_kb`).
- **/schedule backup:** kelas Backup render 🔄 + `backup_hari_tanggal` + "← HARI INI" kalau backup date == today (schedule.py via `_parse_backup_date`).
- **usage enriched:** `usage.log(chat_id, name, action, kode, **extra)` — extra selain 4 arg utama difilter (skip None/""), backward-compat; absen log tambah `pertemuan=, status=, jumlah=len(ids)`.
- **NEXT (user):** cek Railway ACTIVE + tes `/schedule` backup + `/absen` mode/warning + `/stats` (audit usage detail) di @telefasil_bot.

## [2026-09-15] Mode info-only — warning dua arah dihapus — f387b4e (3bc1549..f387b4e)
> Commit `f387b4e` pushed `3bc1549..f387b4e`. HEAD = f387b4e. Railway auto-deploy.
- **Isi:** hapus warning mode dua arah (lanjutan Mode C info-only dari 3bc1549). `[mode]` label TETAP tampil di checklist nama (absen.py `_build_checklist_kb`).
- **Alasan:** hybrid — kelas tercatat OFFLINE bisa ikut online (status O). Soft-check mode = false positive dua arah; kolom C teks bebas tak bisa bedakan murni vs hybrid → mode = info saja, bukan gate.
- **NEXT (user):** cek Railway ACTIVE + tes `/absen` mode label di @telefasil_bot.
- **Peringatan lama tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 conflict); verify byte-level utk non-ASCII.
- **Rules:** kalau attach gambar → STOP, delegate ke vision agent (jangan analisis model non-vision).

## [2026-09-15] Warning mode dua arah DIHAPUS — f387b4e (3bc1549..f387b4e)
- **SHIPPED:** commit `f387b4e` pushed `3bc1549..f387b4e`. HEAD = f387b4e. Railway auto-deploy. Mode info-only.
- **Isi:** lanjutan Mode C info-only — warning mode dua arah dihapus total (soft-check sisa dilepas). `[mode]` label TETAP tampil di checklist nama (absen.py `_build_checklist_kb`).
- **Alasan:** hybrid — kelas tercatat OFFLINE bisa ikut online (status O) → warning mode tak cocok jadi false positive dua arah. Kolom C teks bebas, tak bisa bedakan murni vs hybrid → mode = info saja, bukan gate.
- **NEXT (user):** cek Railway ACTIVE + tes `/absen` mode label di @telefasil_bot.

## [2026-09-15] Fix IndexError baris kosong — 5e7e9ed (f387b4e..5e7e9ed)
- **SHIPPED:** commit `5e7e9ed` pushed `f387b4e..5e7e9ed`. HEAD = 5e7e9ed. Railway auto-deploy.
- **Isi:** fix `IndexError` di read path absen `sheets.py` — **6 guard** utk baris kosong/pendek (baris sheet kosong tak lagi index out of range). Tidak menyentuh logic dedup/multi-blok/write path.
- **Efek:** `/absen` checklist normal lagi.
- **NEXT (user):** cek Railway deploy ACTIVE + tes `/absen` checklist di @telefasil_bot.
- **Peringatan tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 conflict); verify byte-level utk non-ASCII. Attach gambar → STOP, delegate vision agent.
