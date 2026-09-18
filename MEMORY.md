# MEMORY — telegram-sheets-bot (TelefasilBot)

> Per-project memory. Read at cold session start. Append-only.
> Updated: 2026-09-19 (HEAD ba0bddf.. — stats guard ganda user+chat + audit log, akar lolos)

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

## [2026-09-17] Stats default RINGAN + copy start/help SHIPPED 2114d59 (b85db18..2114d59)
> **SHIPPED:** commit `2114d59` pushed `b85db18..2114d59`. HEAD = 2114d59. **Railway deploy MANUAL (auto-deploy off) � klik Deploy Latest Commit ? ACTIVE = 2114d59, lalu tes /stats + /help.**

- **Default /stats = RINGAN (1-2 bubble):** ringkasan eksekutif (aktif X/Y, aksi, sudah-log %, ? lewat, ?? top-3 tunggakan + tertua, "+N fasil lain � lihat /stats detail") + Per menu 1 baris + Jarang pakai (nama saja) + Belum pernah (nama saja, hitung). Balik keluhan "KEPADETAN".
- **/stats detail** = laporan lengkap 8 blok (roster penuh, per menu, jarang, belum pernah, kelengkapan/tunggakan per fasil, cakupan macet, aktivitas). **/stats ringkas** = tetap alias minimal (blok eksekutif saja).
- _parse_args: none -> ("ringan",7); detail -> lengkap; ringkas -> ringkas. Stray `<` di "Jarang pakai <{thr}" dihindari (raw "<" = BadRequest, pakai "kurang dari {thr} aksi") � kelas bug e604184.
- **Copy /start + /help:** ganti judul "Zoom Record Bot / bot pencatat keseharian" ? **TelefasilBot � asisten harian fasil Cakrawala**: catat ngajar, absen, rekap, backup, sampai jadwal, semua dari chat ini. /help sebut SEMUA fitur + notif otomatis + ajak aksi (/zoom). Tombol inline tetap.
- **Verify:** verify_stats_redesign.py **32/32 PASS** (parse baru, light =1-2 bubble + no detail blok, detail 8 blok, chunk =3500 parseable) + verify_start_copy.py (baru) **8/8 PASS** + verify_stats_chunks PASS + verify_html_escape PASS + compileall OK. Stale: verify_stats_html.py rusak pre-existing (panggil _build_report yg tak ada) � tidak disentuh.
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar ? STOP, delegate vision agent. Cavemem MCP down � append manual.

## [2026-09-17] Parser display-name Zoom CU "NNN_Nama_Prodi" — f48e024 (c5eb2bb..f48e024)
> **SHIPPED:** commit `f48e024` pushed `c5eb2bb..f48e024`. HEAD = f48e024. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = f48e024.**

- **Isi — parser display-name Zoom CU format `NNN_Nama_Prodi`:**
  - `_resolve_absen`: regex **nama-core** — pisahkan NIM/tiebreaker dari nama (display-name Zoom acap `NNN_Nama_Prodi`, nama = Nama_Prodi).
  - **Tiebreaker 3-digit-NIM** — nama sama dobel → pilih yang ekor NIM 3-digit match.
  - **Ambiguous TETAP warned** — kandidat tak ter-resolve → warning, bukan diam-diam pilih.
  - **Jalur lama utuh** — NIM exact-penuh + Nama contains (perilaku lama) tidak diubah; parser baru aditif.
  - **Prompt + HELP contoh baru** — copy bimbingan user cara paste daftar format Zoom CU.
- **VERIFIKASI:** stub **8/8 PASS** (offline, tanpa creds). compileall OK.
- **Housekeeping:** `verify_725423d.py` STALE — panggil `reminder_slots` yang sudah tak ada di code current. Arsipkan bila sempat (non-urgent, bukan gate verify).
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE jadi **f48e024**; (2) tes paste daftar Zoom format `NNN_Nama_Prodi` di `/absen` live.
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent. Cavemem MCP down — append manual.

## [2026-09-17] Stats mode buttons + mapping backup/cancel verified — a1b91ff (0307a6c..a1b91ff)
> **SHIPPED:** commit `a1b91ff` pushed `0307a6c..a1b91ff`. HEAD = a1b91ff. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = a1b91ff, lalu tes `/stats` tombol + `/backup` + `/cancel` baru.**

- **Stats mode buttons (`/stats`):** tombol inline **Detail / Ringkas / Refresh** + callback handler — user ganti mode langsung dari bubble, tanpa ketik ulang arg. Admin guard di callback. Stub **19/19 PASS** (offline, tanpa creds).
- **Backup mapping VERIFIED BENAR** (vs header live): kolom **B–J**. Kolom **A dibiarkan** utk "No." manual — tak diisi bot.
- **Cancel mapping SUDAH dibenerin** di commit `0307a6c` (sebelumnya salah posisi vs header).
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE jadi **a1b91ff**; (2) tes `/stats` tombol Detail/Ringkas/Refresh live; (3) tes `/backup` + `/cancel` isi kolom sesuai mapping B–J.
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent. Cavemem MCP down — append manual.
> **SHIPPED:** commit `2e0d542` pushed `f48e024..2e0d542`. HEAD = 2e0d542. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = 2e0d542, lalu tes `/absen` + `/cancel` live.**

- **SYMPTOM:** `/absen` + `/cancel` SILENT HANG (bot tak balas, wedged lock) — user lapor live.
- **FIX — caps timeout di semua lapisan (bot.py/sheets.py):**
  - `_run` lock **acquire cap 90s** — kalau lock tak ke-dapat dalam 90s → pesan jujur "⏳ Bot lagi sibuk..." (bukan hang diam).
  - **Drain cap 30s** — drain task tidak boleh menahan lock tanpa batas (follow-up RACE-1 e686cec, drain unbounded → kini capped).
  - **gspread `set_timeout(10, 30)`** — connect 10s, read 30s.
  - **Drive httplib2 timeout 30** — API call Drive tak hang selamanya.
  - **socket default timeout 30** (socket.setdefaulttimeout) — jaring pengaman terakhir utk semua koneksi.
- **EFEK:** silent hang → kini **⚠️ honest message** (bot balas alih-alih diam). User tahu sedang sibuk + bisa retry.
- **VERIFIKASI:** stub **9/9 PASS** (offline, tanpa creds). compileall OK.
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE jadi **2e0d542**; (2) tes `/absen` + `/cancel` live (pastikan tak ada silent hang lagi).
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent. Cavemem MCP down — append manual.


## [2026-09-17] /rekap REDESIGN � picker dari Zoom Record (bukan kelas minggu ini) � 80bfe2f..HEAD
> **SHIPPED:** pushed. HEAD = 80bfe2f. **Railway deploy MANUAL (auto-deploy off) � klik Deploy Latest Commit.**

- **PICKER BARU /rekap � basis = Zoom Record fasil (col C match), bukan kelas minggu ini:**
  - sheets.zoom_entries(name) async read-only (via _run) � list {kode, subject, tanggal?dd/mm/yyyy, pertemuan, scheme, sks, tipe, dosen, mulai, zoom, catatan, row}. Skip header + baris kosong.
  - Klasifikasi per entri Zoom: **lengkap ? skip; rumpang ? ?? (fix row+gaps dari rekap_row_status); belum ada baris ? ?**. Urut tanggal lama?baru. Paginasi 25/halaman (� Prev / Next �).
  - Tombol label "{??/?} {dd/mm} {kode} p.{pertemuan}". Header "1?? Rekap yang perlu diisi (dari Zoom Record):". Bawah: "? Buat entri lain (di luar daftar)" + "? Batal" ? jalur manual = flow lama (class picker ? steps normal).
  - ?? = mode lengkapi EKSIS (prefill dari baris sheet via _start_fix, update sel). ? = PREFILL dari Zoom: tanggal/dosen/jam/kode/matkul/sks/pertemuan(termasuk "3 dan 4")/tipe (Online?Online, Offline?On-site), skip step pertemuan+tipe, sisa: sesi?peran?bukti?confirm?append (separator logic tetap).
  - Daftar kosong ? "? Semua rekap dari Zoom Record sudah lengkap." + tombol buat entri manual.
  - Callback baru: zk:{idx} pick, zkp:prev|next halaman, zk:manual/zk:cancel. State ZOOM = 8. zoom_tanggal di user_data override tanggal kelas (manual vs zoom flow).
- **VERIFIKASI:** verify_zoom_picker.py **16/16 PASS** (offline, tanpa creds: parsing, klasifikasi 3 status, urutan, paginasi, prefill zoom?record, jalur manual). compileall OK.
- **NEXT (user):** (1) Railway ? **Deploy Latest Commit** ? tes /rekap live: pilih ??/?, pastikan pertemuan "3 dan 4" + tipe On-site masuk, baris baru di bawah separator hitam.
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar ? STOP, delegate vision agent. Cavemem MCP down � append manual.

## [2026-09-17] FIX rekap swap Pertemuan/SKS + label dropdown — 1854543 (f94b5eb..1854543)
> **SHIPPED:** commit `1854543` pushed `f94b5eb..1854543`. HEAD = 1854543. **Railway deploy MANUAL — klik Deploy Latest Commit → ACTIVE = 1854543, lalu tes /rekap (baris baru InVC6 dkk).**

- **PROBE LIVE (read-only) menemukan akar bug — BUKAN zoom picker:**
  - `_zoom_entries` mapping **BENAR**: raw row 397 InVC6 = D 'Jumat, 11 September 2026', H='2', J='3' → parse 11/09 p2 sks3. Kolom Zoom Record live: F=kode, G=mk(formula VLOOKUP MatkulMaster), H='Pertemuan ke-', I='Skema Kelas', J='Jumlah SKS', K='Tipe Kelas', L='Nama Dosen' (formula), M='Jam Mulai', N='Zoom'.
  - **AKAR = tab rekap: header live G='Pertemuan ke-', H='SKS'** tapi `RekapRecord.as_row()` tulis sks→G, pertemuan→H. Bukti: rekap row14 InVC6 = G(prtm)=3, H(sks)=2 padahal truth p2/sks3 → sheet tampil "Pertemuan 3, SKS 2" (persis laporan user). Baris 3-12 (campus) layout benar (G=1,H=3) → cuma baris bot yang korup.
- **FIX:** as_row swap (pertemuan→G idx5, sks→H idx6) + `_rekap_row_status` labels (Pertemuan idx5, SKS idx6) + `_start_fix` prefill meeting dari cell(6)=G (bukan cell(7)) + confirm_cb fix-path vals G=Pertemuan,H=SKS + **guard peran sebelum confirm** (urutan sesi→peran→bukti) + SESI_OPTS diurutkan = opsi dropdown.
- **LABEL DROPDOWN (dataValidation live) SUDAH COCOK — tak perlu ubah:** I/Tipe=['On-site','Online'], J/Sesi=['Kelas Biasa','Guest Lecture','Lainnya','Workshop/E-Lab'], K/Peran=['Fasilitator Kelas','Moderator Guest Lecture','Backup Fasil']. Asumsi user "English chip (Class Meeting/Facilitator Class)" SALAH. `_zoom_tipe` Offline→On-site benar.
- **VERIFIKASI:** verify_rekap_swap.py **12/12 PASS** (baru) + verify_zoom_picker **16/16 PASS** (regresi bersih) + compileall OK. verify_s3.py STALE pre-existing (pakai kwarg `reminder_hour` yang sudah dihapus) — bukan regresi.
- **Data korup existing:** rekap row14 InVC6 (18/9, G=3/H=2) perlu perbaikan manual / isi ulang via bot setelah deploy (path fix hanya isi sel kosong — baris terisi tidak disentuh).
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent.

## [2026-09-18] Auto-registrasi cukup-ketik-nama SHIPPED ba0bddf (ef217b0..ba0bddf)
> **SHIPPED:** commit `ba0bddf` pushed `ef217b0..ba0bddf`. HEAD = ba0bddf. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = ba0bddf, lalu tes user baru ketik nama langsung (tanpa /register).**

- **Onboarding baru — "cukup ketik nama aja":** handler `text_unregistered` ada di **group 1** (PTB: group 0 = ConversationHandler `/register`; group 1 tetap jalan setelah conv selesai — verified).
- **Guard WAITING:** flag `user_data[WAITING]` cegah `text_unregistered` nyelak flow `/register` di group 0. **Guard REG_JUST:** `user_data.pop(REG_JUST) == name` → cegah duplicate lookup group 1 utk nama yang BARU diproses group 0.
- **Tombol `rgn:{i}` di luar conv** — user yang belum terdaftar, ketik nama → bot daftarkan + tampilkan tombol registrasi (rgn picker).
- **/start clue baru** — copy onboarding arahkan "tinggal ketik nama".
- **VERIFIKASI:** stub **15/15 PASS** (offline, tanpa creds) — alur ketik-nama, guard WAITING/REG_JUST, PTB group1 jalan setelah conv.
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE jadi **ba0bddf**; (2) tes live: user baru (belum terdaftar) ketik nama di chat bot → harus langsung daftar tanpa `/register`.
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent. Cavemem MCP down — append manual.

## [2026-09-18] Kelas make-up READ-ONLY SHIPPED ef217b0 (c5eec48..ef217b0)
> **SHIPPED:** commit `ef217b0` pushed `c5eec48..ef217b0`. HEAD = ef217b0. **Railway deploy MANUAL — klik Deploy Latest Commit → ACTIVE = ef217b0, lalu tes `/schedule` + `/zoom` make-up.**

- **`get_all_loggable_classes` → TUPLE-3 (personal, backup, makeup):** 6 call-site di-update (absen picker, schedule, reminder, zoom, rekap, cancel). Kelas make-up = kelas cancel milik user yang dijadwal ulang (tag L).
- **Label make-up:** badge `🧪` (bukan emoji lain) di tombol picker & baris jadwal.
- **`/schedule`:** render tanggal L (tanggal make-up) + catatan make-up utk kelas cancel milik user.
- **Support make-up:** `/reminder`, `/zoom`, `/rekap`, `/absen` paham kelas make-up (scheme/tipe dari kelas asal).
- **Kolom J filter:** kelas dengan J = `true`/`t` (exclude) TIDAK pernah ikut picker/loggable — bukan sekadar tampilan, read path memfilter.
- **WRITE path TIDAK disentuh:** tulis J–R untuk kelas make-up TIDAK PERNAH dilakukan bot (read-only integration, by design).
- **VERIFIKASI:** stub **20/20 PASS** (offline, tanpa creds) — expand tuple-3, filter J, label 🧪, schedule tanggal L, cancel-milik-user.
- **Skew git:** range `c5eec48..ef217b0` — `c5eec48` tak tercatat di memory (terakhir `f94b5eb..1854543`). Pola rebase/force-push existing — verifikasi `git log --oneline -8` sebelum asumsi.
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE jadi **ef217b0**; (2) tes `/schedule` kelas make-up (tanggal L + catatan); (3) tes `/zoom` pilih kelas make-up 🧪.
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent. Cavemem MCP down — append manual.

## [2026-09-19] Stats guard ganda user+chat + audit log
> **SHIPPED:** fix guard /stats & /darurat. **Railway deploy MANUAL — klik Deploy Latest Commit → ACTIVE = HEAD, lalu tes /stats switch akun.**

- **LAPORAN USER:** akun fasil lain kirim /stats → TETAP dapat laporan lengkap (15/20 fasil aktif, 171 aksi). Guard lama `effective_chat.id != ADMIN_ID` (stats.py) diduga lolos di build yang jalan.
- **INVESTIGASI AKAR (git):** `git log -S ADMIN_ID -- handlers/stats.py` = cuma 2 commit (a1b91ff, 44e647a). `git show` SEMUA versi committed (44e647a, b978525, e5233a6, ..., ba0bddf) — guard `effective_chat.id != ADMIN_ID` ADA + indent benar + `return` ada sejak ROOT. **TIDAK ADA versi guard `if False`/None/salah-indent/return-lupa di repo ini.**
- **KENAPA LOLOS:** (1) `eedeb629` — commit deploy ACTIVE pertama per MEMORY [2026-09-11] — **MISSING dari git repo** (unknown revision). Riwayat ter-rebase/force-push (pola skew existing; lihat entri 4e9e344/88d1fee/e686cec). Build Railway ACTIVE TIDAK bisa diverifikasi dari repo — kemungkinan besar jalan dari commit pre-rebase dengan stats.py beda/tanpa guard. (2) Kelemahan struktural guard lama: HANYA cek `effective_chat.id`. Di PRIVATE chat id = user id → benar; di GROUP chat id != user id → salah principal (cek chat, bukan user). (3) verified via stub — guard ganda None-safe.
- **FIX (stats.py):** guard ganda `_admin_ok(uid, cid)` — izinkan HANYA jika `effective_user.id == ADMIN_ID ATAU effective_chat.id == ADMIN_ID`; keduanya beda → DITOLAK + `log.warning("stats denied chat=%s user=%s (@%s)")`. Jalur sukses: `log.info("stats served to chat=%s user=%s mode=%s")` (cmd & callback) — audit trail permanen di Railway. `_actor()` None-safe (stub lama tetap pass).
- **FIX (darurat.py):** guard lama sama `effective_chat.id` → di-upgrade guard ganda + log denied (entry admin-only lain yang TERGUARD tapi same-principal bug).
- **Entry lain:** /stats cuma 1 CommandHandler (bot.py BotCommand menu OK) + 1 CallbackQueryHandler pattern ^st: — tak ada jalur kedua tanpa guard.
- **VERIFIKASI:** `py -m compileall` OK + stub **verify_stats_guard.py 13/13 PASS** (cmd/cb non-admin deny+log, admin served+log, OR user/chat, unknown cb) + **verify_stats_callback.py 19/19 PASS** regresi. verify*.py tidak di-track (pola existing).
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE; (2) tes live: akun fasil lain /stats → ⛔ + log denied; admin → sukses + log served; (3) cek Railway log utk baris `stats denied`/`stats served`.
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent.
