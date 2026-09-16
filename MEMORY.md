# MEMORY — telegram-sheets-bot (TelefasilBot)

> Per-project memory. Read at cold session start. Append-only.
> Updated: 2026-09-17 (HEAD a37a294 — html.escape sweep 42 titik 7 handler; Railway deploy MANUAL)

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
- Validator Railway juga flag token `VOLUME`/`volume` di KOMENTAR Dockerfile (error "at Line 15"). JANGAN tulis kata `volume` di file Dockerfile sama sekali (a022e30).
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
## [2026-09-15] Verifikasi offline HEAD 725423d (no creds, no deploy) — PASS
> Commit `725423d` (worksheet-scoped batch writes + grid guards + _SYSTEM_TABS). Verifikasi tanpa creds live:
- **compileall** OK (py 3.12.10, `py -m compileall -q .`).
- **Import 19 modul** OK — config, sheets, users, usage, bot, dan seluruh handlers/* (deps ada: gspread 6.2.1).
- **Stub test `verify_725423d.py` (baru)** — gspread full-stub (FakeClient/Spreadsheet/Worksheet), tanpa creds: **18/18 PASS**. Cek: write ranges semua qualified `'{Title}'!A1` (append_record 10 kolom, update_absen multi-blok, update_rekap_cells), empty rows tak IndexError (separator baris kosong), multi-blok agregat lintas tab (D5/D10/D4 correct), grid guard raise + full-tab append gagal bersih.
- **Grep findings:**
  - `values_batch_update` 3 call semua qualified range (L445 `_append_record`, L766 `_update_absen`, L1005 `_update_rekap_cells`) + `values_batch_get` L591 qualified. PASS.
  - `[0]` tanpa guard: 0 — semua akses row-list guarded (`if r and ...`, ternary `len()>0`, `if len==1`, split[""] non-empty, `blocks if else`). PASS.
  - bare `except:` 16 lokasi — SEMUA best-effort UI (hapus loading/busy msg, fallback edit, usage log); bukan path I/O kritis. WARN-only, konvensi `except Exception` belum dipakai di path ini.
- No deploy. Working tree: +`verify_725423d.py` (test stub).

## [2026-09-15] Review S3 — grid guard backup/cancel + callback idx bounds (725423d..)
> **SHIPPED:** commit ini. Railway auto-deploy.
- **Isi:**
  - `sheets.py` `_append_backup_record` / `_append_cancel_record` kini panggil `_guard_grid` (`J` / `I`) sebelum `ws.update` — tab penuh/salah gagal bersih, bukan tulis di luar grid.
  - Bounds check callback idx di: `log.pick_class`, `rekap.pick_class`/`pick_sesi`/`pick_peran`, `cancel.pick_class`, `backup.pick_class`, `absen.toggle_check` — OOB → pesan expired + END (sesi/peran → re-ask KB), tak ada IndexError.
  - Fix bonus: `rekap.back_to_rk_sesi` tombol "Kelas Biasa" callback `rks:biasa` (tak match pattern `rks:\d+`, tombol mati) → `rks:0`.
  - Optional: 16 bare `except:` → `except Exception` (best-effort UI path tetap), hapus dead code `update_chat_id` (backup.py).
- **Verifikasi:** `py -m compileall -q .` OK. Stub lama `verify_725423d.py` 18/18 PASS (regresi bersih). Stub baru `verify_s3.py` 15/15 PASS (guard grid backup/cancel + 7 OOB handler checks via FakeUpdate/FakeCtx, tanpa creds).
- No deploy. Working tree: +`verify_s3.py` (test stub, uncommitted seperti pola sblmnya).

## [2026-09-15] S3 bounds+grid fix SHIPPED 6e952b7 (725423d..6e952b7)
- **SHIPPED:** commit `6e952b7` pushed `725423d..6e952b7`. HEAD = 6e952b7. Railway auto-deploy.
- **Isi:** (1) `_guard_grid` di `_append_backup_record` (J) / `_append_cancel_record` (I) — tab penuh/salah gagal bersih, tak tulis di luar grid; (2) bounds check callback idx → 7 path OOB handler (`log.pick_class`, `rekap.pick_class/pick_sesi/pick_peran`, `cancel.pick_class`, `backup.pick_class`, `absen.toggle_check`) — OOB → pesan expired + END (sesi/peran re-ask KB), tak IndexError; (3) 16 bare `except:` → `except Exception` (path UI best-effort tetap); (4) dead code `update_chat_id` (backup.py) dihapus; (5) fix `rks:biasa` → `rks:0` (tombol "Kelas Biasa" mati).
- **Verifikasi (offline, tanpa creds):** `verify_725423d.py` **18/18 PASS** (regresi bersih) + `verify_s3.py` **15/15 PASS** (grid guard backup/cancel + 7 OOB handler). `py -m compileall -q .` OK.
- **NEXT (user):** cek Railway ACTIVE + tes live SEMUA fitur di @telefasil_bot.
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent.

## [2026-09-15] Guard longgar + escape apostrof + usage fail-open + tombol retry — 892b39c (6e952b7..892b39c)
- **SHIPPED:** commit `892b39c` pushed `6e952b7..892b39c`. HEAD = 892b39c. Railway auto-deploy. Review (lanjutan S3/UX).
- **Isi:**
  1. **Guard baris longgar** — guard baris kosong/pendek dilonggarkan (jangan reject baris valid yang cuma kurang 1-2 cell trailing; tetap aman utk baris benar-benar kosong). Lanjutan fix `IndexError` 5e7e9ed.
  2. **Escape apostrof** — input user dengan apostrof (`'`) di-escape sebelum tulis/query Sheets (hindari error formula/parse + input terpotong).
  3. **Usage fail-open** — path logging `usage.py` dibuat fail-open: gagal tulis usage TIDAK boleh menggagalkan/memblok aksi bot utama.
  4. **Tombol retry** — tombol retry ditambahkan di UI saat aksi gagal (user bisa ulang tanpa restart flow).
- **Verifikasi:** `py -m compileall -q .` + stub offline (tanpa creds).
- **NEXT (user):** (1) cek Railway deploy **ACTIVE**; (2) tes `/zoom` submit di @telefasil_bot; (3) tes `/absen`.
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent. Cavemem MCP down — append manual.

## [2026-09-15] Fix lookup zoom kelas backup — rombel-match 4e9e344 (a022e30..4e9e344)
> **SHIPPED:** commit `4e9e344` pushed `a022e30..4e9e344`. HEAD = 4e9e344. **Railway redeploy needed (auto-deploy belum pasti).**

- **AKAR BUG:** `_fetch_backup_classes` lookup master by Kode saja → kode yang muncul di >1 RomBel (col 8) selalu ambil baris pertama → zoom kelas backup salah.
- **FIX 1 — rombel-match** (`sheets.py` `_fetch_backup_classes` ~L810-838): kumpulkan SEMUA master row dgn kode sama (casefold); `room_key = _normalize(row[7])` = backup Col H; pilih master row yang `_normalize(mr[COL_ROMBEL]) == room_key`; tanpa match → `candidates[0]` (fallback lama, perilaku sengaja dipertahankan).
- **FIX 2 — keterangan fallback master** (`sheets.py` ~L853): `keterangan = (row[9] backup Catatan / Col J) or ket_master` → **catatan backup MENANG**, master Keterangan cuma fallback.
- **FIX 3 — zoom_label** (`ClassEntry.zoom_label`, `sheets.py` L107-116): priority `Keterangan` regex `Zoom\s*(\d+)` (case-insensitive) → `Zoom {n}`; fallback `Nomor Zoom`; kosong → `""`. Link di Keterangan diabaikan. Efek: zoom yang cuma ada di master Keterangan (col 12) kini terbaca utk kelas backup.
- **KASUS NYATA:** **Ali Morteza** — zoom **28 → 40** (rombel-match: kode sama, baris pertama = RomBel 3 → Zoom 28; baris match Col H = RomBel 5 → Zoom 40).
- **VERIFIKASI:** stub `verify_backup_master.py` **5/5 PASS** (1 rombel match, 2 fallback first, 3 zoom dari Keterangan, 4 catatan backup menang, 5 fallback Nomor Zoom) + `py -m compileall -q .` OK. Offline, tanpa creds.
- **NEXT (user):** (1) deploy `4e9e344` di Railway → cek ACTIVE; (2) tes live backup: pilih kode kelas backup dengan RomBel spesifik di `/absen` (picker 🔄) → verifikasi zoom label benar.
- **⚠️ SKEW GIT:** entri `892b39c` mencatat HEAD 892b39c; range push tercatat `a022e30..4e9e344` → kemungkinan rebase/force-push atau origin/main reset. **Verifikasi `git log --oneline -5` + `git status` sebelum asumsi.**
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent. Cavemem MCP down — append manual.

## [2026-09-15] Railway deploy state — ACTIVE = a022e30; 4e9e344 BELUM deploy
- **RAILWAY ACTIVE (user screenshot):** deployment = **a022e30** "fix: strip VOLUME token from Dockerfile comments". Service **Online**, volume attached, trial **26 hari** tersisa, region **US West**.
- **4e9e344 (zoom backup rombel-match) BELUM deploy:** Railway masih jalan `a022e30` — **auto-deploy off / deploy manual trigger**. Perlu klik **Deploy Latest Commit** di Railway lagi.
- **HEAD git vs Railway:** origin/main HEAD = `4e9e344`; Railway ACTIVE = `a022e30`. Gap 1 commit.
- **NEXT (user):** (1) Railway → Deploy Latest Commit → cek ACTIVE jadi `4e9e344`; (2) tes backup zoom label (Ali Morteza: zoom harus **40**, bukan 28).

## [2026-09-16] README rewrite 701 baris — 8a1fa40 (1347178..8a1fa40)
> **SHIPPED:** commit `8a1fa40` pushed `1347178..8a1fa40`. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = 8a1fa40.**

- **README ditulis ulang total:** 701 baris (sebelumnya tipis), TOC 10 bagian — panduan umum (fasil: Mulai Cepat, Cara Pakai per Fitur, Notifikasi, Bukti Foto, Fitur Pintar) + teknis (admin/dev: Prasyarat, BotFather, Google SA, share sheets/Drive, .env lengkap, run lokal Win/Linux/macOS, deploy Railway, Troubleshooting, Struktur Project, Catatan Teknis).
- **Rekap 16 commit sesi diminta user** — 16 commit sesi (mulai a022e30 → 8a1fa40) di-rekap & disajikan ke user.
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE jadi **8a1fa40**; (2) tes live di @telefasil_bot (verifikasi panduan sesuai perilaku nyata); (3) **share panduan ke grup fasil** (link t.me bot / file README).
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent. Cavemem MCP down — append manual.

## [2026-09-16] Reminder multi-slot SHIPPED e2f45ce (4e9e344..e2f45ce)
> **SHIPPED:** commit `e2f45ce` pushed `4e9e344..e2f45ce`. HEAD = e2f45ce. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit.**

- **Isi — reminder 3 slot/hari:**
  - Env **`REMINDER_TIMES="21,5,13"` (UTC)** = **04:00, 12:00, 20:00 WIB** (21+7=04 same day, 5+7=12, 13+7=20).
  - **Slot pagi (04:00 WIB):** kirim jadwal PENUH (semua kelas).
  - **Slot siang/malam (12:00 & 20:00 WIB):** hanya kelas **belum di-log** (skip kalau semua sudah beres).
  - **Job name unik per slot** (mis. `reminder:04` / `reminder:12` / `reminder:20`) — PTB JobQueue tak bentrok, stop/restart per-slot bukan all.
  - **Backward compat env lama:** `REMINDER_TIMES` kosong/tak ada → perilaku lama (single 05:00 WIB jadwal penuh) tetap jalan.
  - **Heartbeat TIDAK disentuh** (job 05:00 WIB tetap).
- **Verifikasi: 22 checks PASS** (stub offline, tanpa creds).
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE jadi **e2f45ce**; (2) tes live: reminder pagi kirim jadwal penuh, siang/malam cuma kelas belum di-log; (3) cek heartbeat tetap jalan.
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent. Cavemem MCP down — append manual.

## [2026-09-16] Drive scope fix — 403 upload foto/doc → cccfd0a (a933f96..cccfd0a)
> **SHIPPED:** commit `cccfd0a` pushed `a933f96..cccfd0a`. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = cccfd0a.**

- **Isi:** `sheets.py` L55-58 `SCOPES` — `https://www.googleapis.com/auth/drive.file` → `https://www.googleapis.com/auth/drive` (full Drive scope).
- **AKAR 403 (bukan permission SA):** scope `drive.file` hanya mengizinkan akses file/folder yang DIBUAT bot sendiri. Folder Bukti existing (dibuat manual oleh user/admin) DITOLAK — Service Account sebagai **Editor** pun tak bisa tulis. Gejala: upload foto/dokumen 403 walau SA sudah di-share Editor.
- **Fix = scope saja, TANPA re-auth** (service account, bukan OAuth user consent).
- **Pelajaran:** `drive.file` aman tapi tak bisa sentuh folder pre-existing. Kalau bot menulis ke folder yang dibuat manual → WAJIB scope `drive` penuh, atau buat folder lewat bot sendiri.
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE jadi **cccfd0a**; (2) tes upload foto + dokumen di @telefasil_bot.
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent. Cavemem MCP down — append manual.

## [2026-09-16] Absen: nama pengisi di header kolom + stale guard → 88d1fee (1260db5..88d1fee)
> **SHIPPED:** commit `88d1fee` pushed `1260db5..88d1fee`. HEAD = 88d1fee. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = 88d1fee.**

- **Isi — nama pengisi di header kolom pertemuan:**
  - Sel header kolom pertemuan (dulu berisi **angka** pertemuan) kini diisi **nama pengisi** — konvensi sheet yang dipakai sheet absen. Multi-blok: ditulis ke SEMUA blok pertemuan yang match (bukan hanya blok pertama).
  - Reply konfirmasi absen menyertakan **+Pengisi** (nama pengisi yang tercatat).
- **Stale guard + hapus tombol (stop double-tap):**
  - Guard state **stale** pada callback absen → cegah **double-tap** memicu `KeyError` (state/sesi sudah tidak valid saat tombol kedua ditekan).
  - Tombol dihapus setelah ditekan → tap kedua tak bisa diproses (bukan cuma di-guard).
- **Verifikasi:** stub **15/15 PASS** (offline, tanpa creds).
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE jadi **88d1fee**; (2) tes `/absen` di @telefasil_bot; (3) cek **nama pengisi tampil di header kolom pertemuan**.
- **⚠️ SKEW CATATAN:** range push tercatat `1260db5..88d1fee` — `1260db5` tidak muncul di entri memory sebelumnya (entri terakhir menyebut `cccfd0a`/`a933f96..cccfd0a`). Indikasi rebase/force-push atau commit perantara tak tercatat. Verifikasi `git log --oneline -8` + `git status` di sesi code berikutnya sebelum asumsi.
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent. Cavemem MCP down — append manual.

## [2026-09-16] RACE-1 orphan fix SHIPPED e686cec (e720c7c..e686cec)
> **SHIPPED:** commit `e686cec` pushed `e720c7c..e686cec`. HEAD = e686cec. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = e686cec.**

- **Isi — RACE-1 orphan fix:**
  - `_run` shield + drain **under lock** (race: task orphaned/duplicate saat lock timeout/exception — single writer dilanggar).
  - Stub **PASS** (offline, tanpa creds) — pola verifikasi stub existing.
- **Follow-up dicatat:** drain **unbounded** — cap jumlah drain selama lock (opsional, nanti).
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE jadi **e686cec**; (2) tes live minimal path gspread write (log/rekap/absen/backup/cancel).
- **⚠️ SKEW GIT:** range `e720c7c..e686cec` — `e720c7c` tak muncul di entri memory sebelumnya (terakhir `1260db5..88d1fee`). Indikasi rebase/force-push atau perantara tak tercatat. Verifikasi `git log --oneline -8` + `git status` di sesi code berikutnya sebelum asumsi.
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent. Cavemem MCP down — append manual.

## [2026-09-16] Stats crash guards SHIPPED e6e45e6 (e686cec..e6e45e6)
> **SHIPPED:** commit `e6e45e6` pushed `e686cec..e6e45e6`. HEAD = e6e45e6. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = e6e45e6, lalu tes `/stats`.**

- **Isi — crash guards `/stats`:**
  - **Chunk hard-split 3800** — chunk stats dipecah paksa di 3800 (label `CEB="${n}"`), tak ada chunk > limit Telegram → pesan stats tak terpotong/gagal kirim.
  - **Corrupt entry skip + warning** — entri usage rusak/corrupt di-skip + warning ditampilkan (bukan crash seluruh stats).
  - **Drain `BaseException`** — drain path menangkap `BaseException`, sedangkan `_run` tetap `except Exception` (blok drain ditinggikan — tak ada task/exception bocor dari drain).
  - **Chunk len log** — log panjang chunk tiap kirim (debug/deploy QA).
- **Verifikasi:** stub **7/7 PASS** (offline, tanpa creds) — pola stub existing.
- **⚠️ ROOT PRIMER:** Railway ACTIVE **masih `e686cec`** (atau lebih lama) — **live ≠ HEAD e6e45e6**. User WAJIB **Deploy Latest Commit** → ACTIVE = `e6e45e6` → tes `/stats` live. Semua entri sesi sebelumnya: deploy manual, auto-deploy off.
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent. Cavemem MCP down — append manual.

## [2026-09-17] html.escape sweep SHIPPED a37a294 (e6e45e6..a37a294)
> **SHIPPED:** commit `a37a294` pushed `e6e45e6..a37a294` (via `e0e2093` chunk fix, `e604184` stats fix). HEAD = a37a294. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = a37a294, lalu tes `/stats` + semua flow.**

- **Isi — html.escape sweep:**
  - **42 titik escape di 7 handler** — input user (nama, kode, catatan, dll) di-`html.escape` sebelum kirim ke Telegram (parse_mode HTML) → cegah broken markup / user-content jadi markup.
  - **Verify:** stub `verify_html_escape.py` PASS, **literal `<{` sisa = 0** (grep literal).
  - Commit perantara range: `e0e2093` chunk fix, `e604184` stats fix.
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE jadi **a37a294**; (2) tes `/stats` live; (3) tes semua flow (log/rekap/absen/backup/cancel/schedule/reminder).
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent. Cavemem MCP down — append manual.

## [2026-09-17] Stats redesign + coverage fix SHIPPED cfbdd59 (a37a294..cfbdd59)
> **SHIPPED:** commit `cfbdd59` pushed `a37a294..cfbdd59`. HEAD = `cfbdd59`. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = cfbdd59, lalu tes `/stats` + `/stats detail`.**

- **Audit bug absen coverage (live):**
  - **ROOT CAUSE (bukan deteksi gagal):** `_absen_coverage` menghitung **baris nama pengisi** (row tepat di bawah baris nomor sesi, tanpa NIM) sebagai pertemuan terisi → **21 kode overcount** (contoh DS01 tampil [2] padahal kosong). Kolom offset (3+(p-1)) SUDAH benar vs baris nomor sesi. Blok kosong tidak ikut.
  - **"Semua 405 tampil 0/16" = artefak tampilan:** report sort ASCENDING + hanya 10 teratas → user cuma lihat yang termacet. Data asli: 247/405 non-empty, 158 kosong (160 setelah fix, DS01 dkk benar-benar kosong).
  - **WDC05 TIDAK ADA** di absen (15 tab), jadwal, maupun Zoom Record batch ini → tak bisa diverifikasi (bukan bug bot).
  - **Fix:** `sheets._absen_coverage` skip row tanpa NIM/Nama (guard sama dgn `_absen_counts`) — verifikasi live inflasi 21→0, cov == raw scan.
- **Redesign `/stats`:** default = ringkas 1 bubble (Aktif X/Y, Sudah-log %, lewat, 🔴 Perlu perhatian top tunggakan + tertua, ✅ Beres semua nama, Absen macet N, bar 7 hari cap 40). `/stats detail` = roster penuh + matriks + tunggakan per fasil (kode saja, 📅 tanggal sekali per grup) + cakupan macet saja + bar. Arg `detail|rinci|full|lengkap`, periode tetap (`/stats detail bulan`). Escape + chunk 3500 line-boundary tetap.
- **Verify:** stub `verify_stats_redesign.py` **16/16 PASS** (offline fake gspread + render); `verify_stats_chunks.py` PASS; py_compile semua file OK.
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent. Cavemem MCP down — append manual.
