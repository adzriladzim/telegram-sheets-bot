# MEMORY — telegram-sheets-bot (TelefasilBot)

> Per-project memory. Read at cold session start. Append-only.
> Updated: 2026-09-26 (backup fix delegasi PPC01 + week filter **SHIPPED ead798d**; dirty: backup.py/cancel.py/register.py/sinkron.py/PDF PPC01; deploy MANUAL belum diklik)

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

## [2026-09-19] Legenda ikon HELP + mini-legenda picker SHIPPED 873d961 (d2ac5d6..873d961)
> **SHIPPED:** commit `873d961` pushed `d2ac5d6..873d961`. HEAD = 873d961. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = 873d961.**

- **Isi — legenda ikon:**
  - **Blok HELP 📌 9 ikon** — blok keterangan ikon di pesan `/help` (9 ikon dijelaskan).
  - **Mini-legenda 4 picker** — header picker menampilkan mini-legenda 1 baris: `/zoom` (`⭐ kelas sendiri · 🔄 backup · 🧪 make-up · ✅ sudah`), `/rekap` (`🧩 lengkapi · ➕ buat baru · 🔄 backup · 🧪 make-up`), `/absen` (`⭐ sendiri · 🔄 backup · 🧪 make-up`), `/schedule` (`🔄 backup · 🧪 make-up`). README L433.
  - **README 3.10 "Keterangan ikon"** (L417) — section ikon lengkap.
- **Verify:** grep README 3.10 + mini-legenda (L433) — ada di disk.
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE jadi **873d961**; (2) **share draf legenda ke grup WA** (draf legenda utk fasil).
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-19] /zoom picker grup bertingkat SHIPPED d2ac5d6 (ace8478..d2ac5d6)
> **SHIPPED:** commit `d2ac5d6` pushed `ace8478..d2ac5d6`. HEAD = d2ac5d6. **Railway deploy MANUAL — klik Deploy Latest Commit → ACTIVE = d2ac5d6, lalu tes /zoom picker live.**

- **Picker /zoom = 3 grup bertingkat:**
  - **📅 MINGGU INI** — kelas minggu ini (flow normal).
  - **⏳ SEBELUMNYA** — kelas belum di-log (tunggakan).
  - **✅ SUDAH DI-LOG** — hidden default, toggle **👁 tampilkan / 🙈 sembunyikan**.
- **Done key:** penentuan sudah/belum di-log pakai key SAMA dgn `get_done_by_date` (konsisten — satu sumber kebenaran, tak dobel logika).
- **VERIFIKASI:** stub **11/11 PASS** (offline, tanpa creds).
- **Catatan minor:** header grup kosong mungkin ikut tampil (contoh render) — cek live, hide kalau iya.
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE jadi **d2ac5d6**; (2) tes /zoom: grup 📅/⏳ tampil, toggle 👁/🙈 utk ✅ SUDAH DI-LOG.
- **Rules tetap:** sync gspread di update handler = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); attach gambar → STOP, delegate vision agent. Cavemem MCP down — append manual.

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
> **SHIPPED:** commit `ace8478` pushed `ba0bddf..ace8478`. HEAD = ace8478. **Railway deploy MANUAL — klik Deploy Latest Commit → ACTIVE = ace8478, lalu tes /stats switch akun.**

- **LAPORAN USER:** akun fasil lain kirim /stats → TETAP dapat laporan lengkap (15/20 fasil aktif, 171 aksi). Guard lama `effective_chat.id != ADMIN_ID` (stats.py) diduga lolos di build yang jalan.
- **INVESTIGASI AKAR (git):** `git log -S ADMIN_ID -- handlers/stats.py` = cuma 2 commit (a1b91ff, 44e647a). `git show` SEMUA versi committed (44e647a, b978525, e5233a6, ..., ba0bddf) — guard `effective_chat.id != ADMIN_ID` ADA + indent benar + `return` ada sejak ROOT. **TIDAK ADA versi guard `if False`/None/salah-indent/return-lupa di repo ini.**
- **KENAPA LOLOS:** (1) `eedeb629` — commit deploy ACTIVE pertama per MEMORY [2026-09-11] — **MISSING dari git repo** (unknown revision). Riwayat ter-rebase/force-push (pola skew existing; lihat entri 4e9e344/88d1fee/e686cec). Build Railway ACTIVE TIDAK bisa diverifikasi dari repo — kemungkinan besar jalan dari commit pre-rebase dengan stats.py beda/tanpa guard. (2) Kelemahan struktural guard lama: HANYA cek `effective_chat.id`. Di PRIVATE chat id = user id → benar; di GROUP chat id != user id → salah principal (cek chat, bukan user). (3) verified via stub — guard ganda None-safe.
- **FIX (stats.py):** guard ganda `_admin_ok(uid, cid)` — izinkan HANYA jika `effective_user.id == ADMIN_ID ATAU effective_chat.id == ADMIN_ID`; keduanya beda → DITOLAK + `log.warning("stats denied chat=%s user=%s (@%s)")`. Jalur sukses: `log.info("stats served to chat=%s user=%s mode=%s")` (cmd & callback) — audit trail permanen di Railway. `_actor()` None-safe (stub lama tetap pass).
- **FIX (darurat.py):** guard lama sama `effective_chat.id` → di-upgrade guard ganda + log denied (entry admin-only lain yang TERGUARD tapi same-principal bug).
- **Entry lain:** /stats cuma 1 CommandHandler (bot.py BotCommand menu OK) + 1 CallbackQueryHandler pattern ^st: — tak ada jalur kedua tanpa guard.
- **VERIFIKASI:** `py -m compileall` OK + stub **verify_stats_guard.py 13/13 PASS** (cmd/cb non-admin deny+log, admin served+log, OR user/chat, unknown cb) + **verify_stats_callback.py 19/19 PASS** regresi. verify*.py tidak di-track (pola existing).
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE; (2) tes live: akun fasil lain /stats → ⛔ + log denied; admin → sukses + log served; (3) cek Railway log utk baris `stats denied`/`stats served`.
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent.

## [2026-09-19] FIX header pengisi absen: nh+3, bukan nh+1 — probe live (commit = 6452a28)
> **SHIPPED:** commit `6452a28`. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = 6452a28, lalu tes /absen: nama pengisi mendarat di baris NAMA (di bawah nomor sesi), bukan menimpa angka 1..16.**

- **LAPORAN USER:** FDrw3 sesi 1 di-log — nama pengisi muncul nyempil di deretan ANKA pertemuan, header kolom sesi 1 tak berubah (screenshot).
- **PROBE LIVE READ-ONLY (`verify_pengisi_probe.py`, untracked):** dump FDrw3 (VCD tab) + scan seluruh absen utk nama "Adzril Adzim Hendrynov" & "Muhammad Rayhan F".
  - **GEOMETRI BLOK NYATA (konsisten SEMUA tab):** row `nim_header` (0-based) = "NIM | Nama Mahasiswa | Mode Kelas Asal | Sesi Pertemuan yang Diikuti"; row `nim_header+1` = baris ANGKA pertemuan (D=1..S=16); row `nim_header+2` = baris NAMA pengisi (manusia tulis nama persis di kolom pertemuan, di bawah angkanya). Kolom sesi p = D+(p-1). Bukti hits: FoLA1 (AI) NIM=145, angka=146, nama=147; PrBs3 (IS) 918/919/920; InVC6 (VCD) 396/397/398; PPC01 (IE) 265/266/267 — semua nama manusia di `nh+2`.
  - WDC05 tidak ada di absen (konsisten temuan lama cfbdd59).
  - FDrw3 hanya 1 blok (VCD), baris angka utuh saat probe → nama bot incident sudah dibersihkan manual; geometri tetap terbukti dari blok lain.
- **AKAR BUG 88d1fee:** `_update_absen` tulis header ke `nh + 1` — padahal `nh` 0-based dan A1 1-based → mendarat di baris NIM/angka (menimpa/serempet "Sesi Pertemuan yang Diikuti" & angka 1..16), bukan baris nama `nh+2` (A1 = `nh+3`). Meleset **1 baris ke atas** (bukan masalah kolom).
- **FIX sheets.py:** header_cells `nh + 1` → `nh + 3` (A1) = 0-based `nh+2`. Kolom tetap `3+(pertemuan-1)`. Multi-blok & last-filler-wins dipertahankan.
- **VERIFIKASI:** `verify_absen_header.py` fixture di-update ke geometri nyata (baris angka + baris nama) → **16/16 PASS** (header = `'Ilkom'!D5/D12`, `'Manajemen'!D4`, `G5` utk pertemuan 4; angka tak disentuh). compileall OK. verify_725423d.py STALE pre-existing (kwarg `reminder_hour` dihapus — bukan regresi).
- **NEXT (user):** Railway → Deploy Latest Commit; tes /absen → nama pengisi di baris nama, angka 1..16 tetap utuh.
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent.

## [2026-09-19] Rekap jam range penuh FIX pushed a20817e (6452a28..a20817e)
> **SHIPPED:** commit `a20817e` pushed `6452a28..a20817e`. HEAD = a20817e. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = a20817e, lalu tes /rekap (jam range penuh).**

- **Isi — `_resolve_jam_range` (`handlers/rekap.py` L288):**
  - Konvensi sheet Rekap = jam RENTANG PENUH ("18.30 - 20.00"), tapi Zoom Record col M cuma jam mulai → prefill rekap dulu cuma start-only, kolom D Rekap jadi terpotong.
  - **Priority fix:** cari kelas asli by kode (normalize casefold) di personal/backup/makeup (`get_all_loggable_classes`) — time_range backup/makeup udah EKSPLISIT dari sheetnya → pakai itu; kalau ada → return range penuh. Reuse+cache `user_data['classes']` (manual path fetch sekali, kalau None fetch & simpan). **Fallback:** entri Zoom M (start-only) kalau kode lama tak ketemu di kelas.
  - Efek: prefill ➕ baru + path fix /rekap sekarang tulis jam range penuh, bukan jam mulai.
- **KEPUTUSAN USER:** production **rows 188-193 rekap lama** (jam masih start-only, korup dari bug lama) **TIDAK ditambal via bot** — user putuskan perbaikan manual/bot terpisah. Data korup existing dibiarkan di sheet.
- **VERIFIKASI:** stub `verify_zoom_picker.py` **19/19 PASS** (termasuk kasus `_resolve_jam_range`: match kelas → range penuh, legacy → start-only) + compileall OK.
- **Skew/deploy note:** `6452a28` = FIX header pengisi absen nh+3 (entri atas — hash kini tercatat). **`6452a28` BELUM PASTI ke-deploy** → a20817e juga belum; verifikasi ACTIVE via Railway.
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE jadi **a20817e**; (2) tes `/absen` → nama pengisi di baris bawah angka (header nh+3); (3) tes `/rekap` → kolom jam = range penuh (bukan start-only).
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-19] Repair satu-kali rekap jam production 3516180 DONE — 6 sel start-only → range penuh
> Commit `3516180` (tool `repair_rekap_jam.py`). Lanjutan a20817e (fix kode) — repair DATA existing.

- **LAPORAN USER:** rows 188-193 (screenshot offset tampil = BARIS AKTUAL 14-19) tab **Adzril Adzim** kolom D masih start-only → ditambal SATU-KALI via tool, bukan via bot.
- **REPAIR (6 sel):** InVC6 `18-20`, FDrw3 `20-22`, PR02 `12-14.30`, Jour2 `15.30-18`, Jour3 `20-22`, DtAn1 `08.30-11`.
- **VERIFIKASI LIVE RE-READ:** 0 kandidat start-only tersisa (tool scan ulang bersih).
- **TOOL `repair_rekap_jam.py`** (committed `3516180`): runbook `--selftest` (kering, tanpa tulis) / `--write` (tulis range penuh); reusable utk repair batch serupa.
- **CATATAN OFFSET:** screenshot row 188-193 = tampilan spreadsheet; aktual baris 14-19. Jangan percaya angka baris dari screenshot langsung.
- **SISA:** deploy `a20817e` (fix kode) + `3516180` (tool, bukan runtime) → **ACTIVE di Railway** → tes /rekap baru tulis range penuh.
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-19] Sweep ALL-TABS rekap 0 start-only + fix latent _find_rekap_tab trailing-space — 86edf31
> **SHIPPED:** commit `86edf31`. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = 86edf31 (membawa 6452a28 pengisi nh+3, a20817e jam penuh, 3516180 tool, 86edf31 tab fix).**

- **Sweep SEMUA tab rekap via tool `repair_rekap_jam.py --all-tabs`** — 0 sel start-only tersisa (verifikasi re-read bersih). **1 SKIP:** Ratu r21 IAcc2 — kode tak unik (ambigu di master) → perbaikan MANUAL (tool lapor, tak tulis).
  - Mode baru tool: `--all-tabs` (SEMUA tab rekap, skip `_SYSTEM_TABS`) — tab→nama via `data/users.json` + `_find_rekap_tab`; nama tak ketemu → fallback resolve by KODE scan master (time_range penuh; unik → pakai, ambigu → SKIP & lapor). Cap global 200 sel.
- **FIX latent `_find_rekap_tab` (sheets.py L1276):** nama fasil dengan trailing-space (probe names `"Anisa"`, `"Anisa "`, `"Yodha Adytia Choirullah"`) — tab rekap "Anisa " dkk KINI resolve: `_find_rekap_tab` return **RAW title** (normalize hanya utk compare), worksheet() resolve title raw + cache key konsisten. Sebelumnya: miss → `SheetsError "Tab rekap ... tidak ketemu"`.
- **VERIFIKASI:** probe live `probe_find_rekap_tab.py` (read-only, tanpa creds write) OK — raw title resolve; stub regresi PASS (verify_rekap_border.py + verify_zoom_picker.py regresi bersih).
- **NEXT (user):** Railway → **Deploy Latest Commit** → cek ACTIVE jadi **86edf31**; tes `/rekap` jam range penuh + tab fasil trailing-space resolve (Anisa/Yodha/Dzika).
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-19] Sinkron Feedback SF→S/OF→O SHIPPED 235d3fc (86edf31..235d3fc)
> **SHIPPED:** commit `235d3fc` pushed `86edf31..235d3fc`. HEAD = 235d3fc. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = 235d3fc, lalu tes /rekap (SF ke-S) + /sinkron live.**

- **Sinkron Feedback (SF→S / OF→O) — sheets + rekap:**
  - sheets.py: `feedback_nims` + `_plan_convert_status` / `_convert_status` — shared filter dgn `_feedback_counts`; guard **NIM exact / short-suffix unik** (short-suffix ambigu → tak dipakai diam-diam).
  - Flow: feedback SF/OF → status **S/O** sebelum tulis/cek rekap.
- **Rekap confirm AUTO-SYNC** — feedback di-sinkron otomatis saat confirm rekap; **fail-open** (gagal sync tak blok aksi utama); reply **🔄** saat proses sync.
- **Command `/sinkron <kode> [pertemuan]`** — **preview** rencana konversi + tombol **✅/❌** konfirmasi; registered user only; **timeout 10 menit** (state kedaluwarsa → pesan expired).
- **PROBE LIVE:** NIM **11-digit exact DOMINAN** (format utama di feedback) — guard exact dulu, short-suffix fallback utk yg konsisten unik.
- **VERIFIKASI:** stub **24/24 PASS** (offline, tanpa creds). compileall OK.
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE jadi **235d3fc**; (2) tes `/rekap` → feedback SF ke-S otomatis; (3) tes `/sinkron` manual → preview + ✅/❌.
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-20] Fix /rekap stuck diam allow_reentry (pemicu 2f5e00c) — hash belum tercatat
> **SHIPPED.** **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE, lalu tes /rekap: user yang pernah stuck di alur rekap kini bisa /rekap lagi.**

- **SYMPTOM:** `/rekap` STUCK DIAM (bot tak balas) utk user tertentu setelah commit `2f5e00c` (🔃 refresh). ConversationHandler user tertinggal (stuck) di state **ZOOM return** — state `ZOOM` lama mem-return handler ZOOM menunggu input tak pernah datang.
- **ROOT CAUSE:** `rekap.py:265` — endpoint rekap ZOOM mem-return ConversationHandler ZOOM yang **`allow_reentry` default False** → saat user stuck di state ZOOM, `/rekap` baru DROP SILENT (PTB ConversationHandler tolak re-entry tanpa pesan). Bukan deadlock `_run`/gspread — state-level PTB.
- **FIX:** `rekap.py:989` — ConversationHandler `/rekap` diberi **`allow_reentry=True`** → user yang stuck di alur lain tetap bisa masuk `/rekap` baru.
- **VERIFIKASI:** stub `verify_rekap_refresh.py` **16/16 PASS** (naik dari 14 — kasus re-entry). compileall OK.
- **REVIEW:** **APPROVED** — 2 nit S4 non-blocking: (1) cache clear (invalidate rows setelah refresh), (2) int parse (guard pertemuan parse) — dicatat, bukan gate.
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE; (2) tes live: user yang tadi stuck kirim `/rekap` → harus balas normal.
- **PELAJARAN (konvensi):** setiap ConversationHandler dengan entry point yang bisa dipanggil ulang → set `allow_reentry=True`. State stuck user = silent drop kalau default False.
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-20] Redesign visual kelengkapan minggu ini stats — format-only (hash belum tercatat)
> **SHIPPED.** **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE, lalu tes /stats: blok "Kelengkapan minggu ini" tampil rapi.**
> **Detail:** `handlers/stats.py` L387-399 — blok matriks "Kelengkapan minggu ini".

- **ISI — FORMAT-ONLY:** redesign tampilan blok kelengkapan minggu ini. **Nol logika hitung berubah; kode full tetap; hanya layout/styling baris diubah.**
- **Format baru:**
  - Per nama: baris **`<b>▸ {nama}</b>`** (bold, penanda ▸).
  - **2 baris `<pre>` monospace**:
    - `log   {●●○…} {ld}/{t}` — status per pertemuan (● terisi, ○ mendatang, ✗ lewat) + count done/total.
    - `rekap {●●○…} {rd}/{t}` — sama utk rekap.
  - Baris kode kelas: join **koma+spasi**.
- **Keamanan:** semua nama + kode dari sheet lewat **`html.escape`** → aman dari injection HTML/telegram formatting.
- **VERIFIKASI:** tes/stub **33 PASS** (offline, tanpa creds). compileall OK.
- **REVIEW:** **APPROVED** — 1 nit S4 count saja (non-blocking; catatan soal tampilan count, bukan gate).
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE; (2) tes `/stats` → blok kelengkapan tampil `<pre>` monospace + ●/○/✗ + counts.
- **PELAJARAN:** perubahan visual kecil = FORMAT-ONLY diff kalau logika hitung sudah benar — minimalkan risiko regresi.
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-19] 🔃 refresh angka O–S SHIPPED 2f5e00c (235d3fc..2f5e00c)
> **SHIPPED:** commit `2f5e00c` pushed `235d3fc..2f5e00c`. HEAD = 2f5e00c. **Railway deploy MANUAL (auto-deploy off) — klik Deploy Latest Commit → ACTIVE = 2f5e00c, lalu tes 🔃 di /rekap.**

- **Picker rekap — entri lengkap kini kind `refresh` + tombol 🔃** (`rzkr:{i}`), bukan skip diam-diam:
  - Grup terpisah di bawah ➕/🧩, label "🔃 {dd/mm} {kode} p.{pertemuan}", separator "— 🔃 sudah lengkap (update angka?) —". Klasifikasi 3 status lengkap/rumpang/belum → refresh/fix/new (verify_zoom_picker konsisten).
  - Handler `rekap.pick_zoom_refresh` — user refresh baris rekap lengkap dengan angka feedback terbaru.
- **Diff-only write O–S:** hanya sel kolom O–S yang nilainya berubah yang ditulis (banding lama vs baru dari feedback; `changed` count). **B–L aman** — kolom B–L tak disentuh. **0-diff → no-op** (tak ada perubahan → tak ada write). **Fail-open** — SheetsError saat refresh tak blok aksi utama (reply diff/no-change path).
- **Usage log** aksi `rekap-refresh` + kode.
- **VERIFIKASI:** stub baru `verify_rekap_refresh.py` **14/14 PASS** (refresh_os diff, picker refresh kind+tombol 🔃+separator, handler reply diff/no-change/fail-open, usage) + regresi `verify_zoom_picker.py` PASS + compileall OK.
- **NEXT (user):** (1) Railway → **Deploy Latest Commit** → cek ACTIVE jadi **2f5e00c**; (2) tes /rekap: entri lengkap muncul grup 🔃 → tap → angka O–S ter-update dari feedback.
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-20] telegram-sheets-bot — /laporan PDF beta tersembunyi + 4 S3 fixed [SHIPPED] 2202e4c
> **SHIPPED:** commit `2202e4c` (feat(laporan)) pushed ke main; deploy Railway BELUM (MANUAL oleh user, auto-deploy off).

- **FITUR BARU `/laporan` — Laporan Kinerja Dosen PDF (BETA tersembunyi):**
  - File baru `handlers/laporan.py` (628 baris; docstring: "Alpha direkayasa, silent-gated via env LAPORAN_BETA_IDS + LAPORAN_BETA_NAMES"), terdaftar `handlers/__init__.py` (import L10 + `laporan.register(app, cfg)` L39).
  - `sheets.rekap_rows(tab)` (async read-only via `_run`) — join baca, never write.
  - Deps `requirements.txt`: +**`fpdf2==2.8.8`**, +**`matplotlib==3.10.7`** (pin eksplisit).
  - Fonts repo `fonts/DejaVuSans.ttf` + `DejaVuSans-Bold.ttf` (`_FONT_DIR = BASE_DIR/"fonts"` L48); fallback font bundel matplotlib utk Docker (`laporan.py` L395-404).
- **Gate beta silent (L61-76):**
  - `_beta_ids()` — env `LAPORAN_BETA_IDS`, split **`,`**, int-only.
  - `_beta_names()` — env `LAPORAN_BETA_NAMES`, split **`|`** (nama bisa mengandung koma mis. "S.T., M.T."), casefold.
  - `_beta_allowed(chat_id, name)` — **AND** id DAN nama; gagal → LOG saja + DIAM (login minimal, tanpa reply), `/laporan` TIDAK didaftarkan di help/schedule/BotCommand.
- **Alur (L119-290):** `/laporan` → CLASS picker (personal+backup+makeup; makeup col J!=true sudah difilter) → MEETING picker → join `zoom_entries` + rekap_rows (group by kode col E) + `absen_counts` + `feedback_counts` → `_build_pdf` (thread executor) → sendDocument (⏳ loading dulu) → `usage.log(..., "laporan", kode)`.
- **PDF tiru template contoh FELLA PPC01 (L435-540):** header biru #4558D0, 4 card skor, line chart teal #1A3C40 (`matplotlib.use("Agg")`), tabel kualitatif, footer; font `DejaVuSans` (regular+bold) → UTF-8 aman.
- **`_MATPLOTLIB_LOCK` threading.Lock (L54)** — plt.subplots/close tak thread-safe; `_build_pdf` jalan di thread executor + `concurrent_updates=True` → serialisasi chart (per-chat lock tak cukup).
- **Review APPROVED (beta) + 4 S3 fixed** (detail fix di repo/handlers/laporan.py).
- **VERIFIKASI offline:** `verify_laporan.py` (baru, untracked) — PDF + chart NYATA via fpdf2/matplotlib tanpa network/creds, **22/22 PASS** + compileall OK.
- **Status:** committed `2202e4c` + pushed main; **BELUM deploy** Railway.
- **NEXT (user):** (1) ~~commit fitur~~ commit done `2202e4c`; (2) Railway → **Deploy Latest Commit**; (3) set env `LAPORAN_BETA_IDS` + `LAPORAN_BETA_NAMES` di Railway; (4) tes `/laporan` beta (Fella Amalia / Adzril). `.env.example` L43-44 sudah ada contohnya.
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-20] Beta tester /laporan DITETAPKAN — ID 2061872254 (Adzril) [SHIPPED] 2202e4c
- **Keputusan:** beta tester /laporan = **ID `2061872254`** utk env `LAPORAN_BETA_IDS`.
- **Nama dikonfirmasi dari `data/users.json` L8:** `2061872254` → **"Adzril Adzim Hendrynov"** (casefold `adzril adzim hendrynov`) utk `LAPORAN_BETA_NAMES`.
  - Catatan: ID ini = `ADMIN_ID` yang sudah dipakai `handlers/darurat.py` L15 + `handlers/stats.py` L34.
- **Gate beta (handlers/laporan.py L61-76):** `_beta_allowed(chat_id, name)` = **AND** id DAN nama (casefold). Env format: `LAPORAN_BETA_IDS=2061872254`, `LAPORAN_BETA_NAMES=Adzril Adzim Hendrynov`.
- **Status:** fitur `/laporan` committed `2202e4c` + pushed main; **BELUM deploy** Railway. Setelah deploy, isi entri [2026-09-19] `/laporan` dengan hash baru.
- **NEXT (user):** commit → Railway Deploy → set 2 env → tes `/laporan` sbg beta tester (self-test dgn akun Adzril; Fella Amalia juga id/nama terdaftar di users.json kalau mau 2 tester).
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-21] Hybrid picker /zoom: gap-min + dupe gate + pola warning + 2 S3 (BELUM commit)
> **BELUM commit** — hash belum ada (tunggu instruksi user). Setelah commit, isi hash + SHIPPED. Railway deploy MANUAL selalu (auto-deploy off).

- **get_next_meeting = LANJUT LOGIS gap-min (bukan max+1) — `sheets.py` L693:**
  - `_meeting_rows(kode)` (L663) scan SEMUA baris Zoom Record per kode (kelas bisa dipegang 2 fasil ganjil/genap + backup ikut merekam). `nums` = semua digit dalam teks pertemuan ("3 dan 4" → {3,4}).
  - **Backup TIDAK menggeser progres** (L710): baris tipe Backup di-skip dari `taken`/`last` — backup P3 TIDAK bikin saran kelas loncat. Saran = **angka terkecil yang belum direkam** (gap-min), barisan tak urut aman (P3,P1 + P2 kosong → saran 2, bukan max+1=4). Last = pertemuan max reguler (bukan backup). Kontigu → max+1 tetap benar.
- **Dupe gate (kode, tanggal kelas, pertemuan) — `_find_conflicts` L723:**
  - Key = kode + tanggal (normalized `_norm_date`) + nums **OVERLAP** (set irisan: "3" vs "3 dan 4" kena; beda angka aman). Siapa pun perekamnya — multi-fasil aman.
  - `_conflict_report_text` (log.py L472): lapor **siapa perekam** + baris (fasil, tanggal, pertemuan, tipe) + tawaran: ✏️ Pakai nomor lain (`back:meeting`) / ✅ Tetap simpan (koreksi) (`x:force`) / ❌ Batal. `confirm_force` (L612) set `force_conflict=True` one-shot — di-clear HANYA setelah save sukses (bukan sebelum retry, agar Retry tak minta force ulang).
  - Dupe gate ditanyakan di CONFIRM + warning di step meeting (`_meeting_warnings` L487: pola + dupe, fail-open).
- **Pola warning — `pola.py` BARU (45 baris) + `data/pola.json` (opsional, data/ di-gitignore):**
  - Env `POLA_JSON_PATH` → fallback `data/pola.json`. Schema `{"PPC01": {"pertemuan": {"1": "Fasil A", ...}}}` — nilai = fragmen fasil, dibanding contains casefold.
  - `_pola_warning` (log.py L454): fasil di luar pola → 🟡 **bukan blokir** ("Ingatkan saja, bukan blokir — Yakin tetap lanjut?"); tanpa config/kode → kosong; nama fasil dari config di-html-escape.
  - **Deploy note:** pola.json ada di data/ lokal — Railway butuh env `POLA_JSON_PATH` atau drop ke volume `/app/data` (pola sama dgn users.json).
- **allow_reentry:** conv `/log` diberi `allow_reentry=True` (log.py L654) — user stuck di alur lain tetap bisa masuk /log baru (konvensi lama /rekap rekap.py L989 tetap).
- **2 S3 fixed (tema hybrid picker):** picker pertemuan SELALU tampil (pick_class → MEETING, bukan auto-jump SKEMA — user bisa ubah angka/gate dobel) + make-up read-only filter `J!=true` guard source tetap.
- **VERIFIKASI (offline, tanpa creds):** `verify_zoom_hybrid.py` **17/17 PASS** (gap-min, kontigu, dupe gate + overlap, backup tak geser, pola 4 kasus, picker wiring, allow_reentry, J!=true) + `verify_zoom_picker.py` **22/22 PASS** + `verify_zoom_picker_groups.py` **11/11 PASS** + `verify_zoom_display.py` **8/8 PASS** (regresi). compileall OK.
- **NEXT (user):** (1) commit → hash; (2) Railway → Deploy Latest Commit; (3) env `POLA_JSON_PATH` (kalau pola mau aktif); (4) tes /zoom live: saran pertemuan gap-min, dupe gate lapor perekam, warning pola 🟡.
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-20] fix rumus rating /laporan: mean 1-5 + 2 S3 (BELUM commit)
> Lanjutan /laporan PDF beta `2202e4c`. **Belum commit** — hash belum ada.

- **RUMUS SKOR:** `5*rate` (skala 1-25) → **mean rating 1-5**. Rating feedback = skala 1-5, aggregate = simple mean.
- **2 S3 fixed:**
  1. Filter prodi/school → **shared helper** (dedup absen/feedback path), dipakai konsisten di /laporan.
  2. `_wmean` (weighted) → **`_smean`** (simple avg); test "tak sama" (smean ≠ wmean saat rating bervariasi) PASS + regresi laporan PASS.
- **NEXT (user):** commit → hash jadi ID entri. Railway deploy MANUAL stlh `2202e4c` (auto-deploy off) + set `LAPORAN_BETA_IDS=2061872254` / `LAPORAN_BETA_NAMES=Adzril Adzim Hendrynov` → tes beta.
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-21] Fix /schedule backup basi — window Senin–Minggu WIB (BELUM commit)
> **BELUM commit** — hash belum ada. Setelah commit, isi hash + SHIPPED. Railway deploy MANUAL selalu (auto-deploy off). Review **APPROVED**.

- **SYMPTOM (laporan user):** backup "Selasa, 15 September 2026" TETAP tampil di `/schedule` pada Selasa 20 Sep 2026 — kelas basi (seminggu lalu) masih muncul di hari aktif.
- **ROOT CAUSE:** `_fetch_backup_classes` pulangkan SEMUA baris backup cocok nama (tanpa cek tanggal) + `this_week_classes` (`sheets.py` L1982) grup **by nama hari saja**, tanpa filter tanggal window → backup eksplisit di hari manapun selalu tampil di hari aktif minggu ini.
- **FIX (`sheets.py`):**
  - **`week_span_wib(today)` (L1975)** — `(Senin, Minggu)` rentang minggu WIB berjalan: `mon = t - timedelta(days=t.weekday())`, `sun = mon+6`. `today` param hanya utk test.
  - **`this_week_classes` filter window** — Backup/Make-up dgn `backup_hari_tanggal` eksplisit → parse `_parse_tanggal_panjang` ("Senin, 8 September 2026" → date); di luar `[mon..sun]` → **skip** (tidak tampil). **Fail-open:** tanggal tak bisa di-parse → TETAP tampil (perilaku lama dipertahankan).
  - Personal reguler TIDAK berubah (selalu tampil, mingguan).
- **Zoom/reminder/stats UTUH** — manipulasi hanya di `this_week_classes`; jalur lain (`_class_date`/`_parse_backup_date`/`next_date_for_day`) tak disentuh.
- **VERIFIKASI:** stub baru `verify_schedule_week_window.py` **8/8 PASS** (skenario terbalik semula: backup 15 Sep TIDAK tampil di minggu 20 Sep; backup 20 Sep Minggu sblm window TIDAK; backup 1 Okt minggu depan TIDAK; makeup 16 Sep TIDAK; backup hari ini 21 Sep tampil di Senin; makeup besok 22 Sep tampil di Selasa; personal reguler selalu tampil; fail-open B-BAD "entahlah??" tetap tampil) + **regresi PASS** (stub existing). compileall OK.
- **REVIEW:** **APPROVED.**
- **NEXT (user):** commit → hash jadi ID entri → Railway Deploy Latest Commit → tes `/schedule` live (backup basi hilang, yang dalam window tampil, "← HARI INI" tetap).
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-21] /tukar jadwal fasil TANPA approval � swap tab + first-wins + S2 date-compare + S3 cancel atomik + S4 (BELUM commit)
- **Fitur:** /tukar (catat duluan menang, tanpa approval � sepakati via WA), pola sekali/tetap/jam, /tukar_batal <id> (pencatat/admin), /tukar_riwayat, /tukar_admin (batal/hapus, admin).
- **Storage:** tab baru "Tukar Jadwal" (A..K) di spreadsheet master � dibuat otomatis; master/backup/cancel TIDAK disentuh. Env TUKAR_SHEET_NAME.
- **Read:** get_classes merge swap dulu (sekali/jam date-scoped auto-balik setelah tanggal lewat; tetap = pemilik permanen) � jadwal/zoom/rekap/absen/cancel ikut. Fail-open: tab tak ada = kelas normal.
- **Jaga:** for_chat lock saat tulis, _run single-flight = conflict check + write atomic (rebutan aman), allow_reentry=True, html.escape, WIB (datetime.now(sheets.WIB)), _classes_cache.clear() tiap tulis swap.
- **S2 date-compare (sheets.py):** `_parse_tanggal` (dd/mm/yyyy → `date`; invalid 31/02 → None; sampah → None) — `_merged_classes._sw_still_active` + verify pakai DATE bukan string. Binasa bug string-compare: "6/9/2020" vs "25/9/2020" — `'6/' > '2/'` string TRUE → kelas lama tak balik; date benar balik. `_norm_tanggal` tetap utk normalisasi display/konflik (scale 2-digit).
- **S3 cancel atomik (sheets.py L1651):** `cancel_swap_owned(row_id, by, is_admin)` — lookup + otorisasi (pencatat ATAU admin) + soft-cancel DALAM SATU `_run` (bebas TOCTOU race). Return tuple: (1, owner) ok, (0, "") id tak ada, (-1, owner) sudah BATAL, (-2, owner) bukan pencatat/admin. Dipakai /tukar_batal (handlers/tukar.py L330). `_cancel_swap` lama utk admin (hard=True → batch_clear, riwayat dihapus).
- **S4 typo/dedupe konvensi:** (1) kolom B header tulis "Dicatat" bkn "Dicatat oleh" miring (SWAP_OLAH = Dicatat oleh) — map `_swap_records` konsisten; (2) dedupe insert row: `_append_swap` pilih baris kosong pertama di bawah header (skip baris terisi) → no-dobel-progress; (3) `_swap_records` skip baris tanpa No ATAU Kode saya (header/baris kosong tak ikut).
- **Verif:** `py verify_tukar.py` = **43/43 PASS** (18 kasus inti + 6b S2: parse tanggal, tidak-default-pad 6/9/2020 & 6/9/2099 aktif; 6c S3: cancel_swap_owned 5 kasus owner/admin/none; 7 hygiene: allow_reentry, for_chat, WIB, html.escape, _merged_classes di get_classes). compileall OK; regresi verify_rekap_swap/html_escape/zoom_hybrid/rekap_refresh/schedule_window lolos. BELUM commit � hash nanti. Railway deploy MANUAL selalu.

## [2026-09-23] Fix SKS sesi gabungan — sks×len + repair baris lama jelas-single (BELUM commit)
> **Semua untuk pertemuan gabungan ("3 dan 4" = 2 sesi):** SKS tercatat = SKS dasar, padahal konvensi sheet = SKS × jumlah sesi. JANGAN deploy/jalankan bot — kode + verify offline saja. BELUM commit — hash nanti.

- **FIX `handlers/rekap.py`:** helper baru `_sks_efektif(sks, meeting)` dipakai di `_build_base` — meeting dengan >=2 angka (regex `\d+`) → `sks = int(sks) * len(nums)`; digit guard: SKS bukan angka murni (`re.fullmatch(r"\d+"...)`) ATAU meeting single → biarkan nilai lama apa adanya. Semua jalur `_build_base` ikut benar: ➕ baru, 🧩 fix (prefill), submit, nama file bukti.
- **verify_zoom_picker.py L302:** assert `rec.sks == "2"` → `"4"` (meeting "3 dan 4", sks dasar 2 → 4). `class_from_zoom.sks` (ClassEntry sintetis) TETAP "2" — multiplikasi hanya di RekapRecord.
- **`repair_rekap_sks_gabungan.py` (BARU):** repair baris rekap LAMA kolom H (SKS). Jelas-single: `H == sks dasar Zoom Record J` (satu-satunya nilai utk kode itu di SEMUA baris Zoom, col F=kode col J=sks) DAN pertemuan G >=2 angka → `H = int(H) * len(nums)`. Ambigu (zoom tak ketemu / H!=J / single / H bukan digit) → SKIP & lapor. Mode dry-run (default) / `--write` / `--all-tabs` / `--selftest`; reuse `_retry`, `_collect_tabs`, `_commit_hash` dari `repair_rekap_jam.py`; SAFEGUARD: cuma kolom H, cap 200, skip _SYSTEM_TABS.
- **_refresh_os / absen / feedback / schedule / laporan TIDAK disentuh.**
- **VERIFIKASI:** `py -m compileall -q .` OK; `py verify_zoom_picker.py` **22/22 PASS**; `py repair_rekap_sks_gabungan.py --selftest` **OK** (kandidat gabungan+digit, jelas→×len, ambigu→None 5 kasus, zoom map, hash).
- **REVIEW (update sesi berikutnya):** **APPROVED** — S3 tunggal: **verify-loop acceptable one-time** (loop verifikasi penuh tool repair tak jadi permanent gate; repair sekali utk baris lama — cukup `--selftest` + dry-run + review rencana). BELUM commit — hash nanti.
- **NEXT (user):** koordinasikan jadwal nonaktif dulu (repair tulis langsung ke sheet) → commit → Railway Deploy Latest Commit → jalankan repair `py repair_rekap_sks_gabungan.py` (dry-run dulu, lalu `--write` kalau rencana benar) → tes /rekap live pertemuan gabungan baru (SKS tampil ×sesi).
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-23] Absen sekali-gabungan — parse "3 dan 4"/"3-4" + tulis dua kolom + confirm marker (BELUM commit)
> **BELUM commit** — hash belum ada. Setelah commit, isi hash + SHIPPED. Railway deploy MANUAL selalu (auto-deploy off). Review **APPROVED**.

- **ISI — absen pertemuan gabungan SEKALI tulis dua kolom:** pertemuan `"3 dan 4"` / `"3-4"` (2 sesi) → 1x absen tulis/tandai **KEDUA kolom pertemuan** di sheet absen (bukan cuma kolom pertama).
- **Parse format gabungan:** word `dan` + dash `-` (keduanya didukung).
- **Confirm marker** tampil utk kedua kolom.
- **Hubungan sesi gabungan:** lanjutan konvensi [2026-09-23] Fix SKS sesi gabungan (rekap, `_sks_efektif`) — sekarang sisi absen menulis ke semua kolom pertemuan match.
- **VERIFIKASI:** **23+16 PASS** (kasus gabungan + regresi; offline, tanpa creds). compileall OK.
- **REVIEW: APPROVED** — 2 S3 sanitasi follow-up (non-blocking).
- **NEXT (user):** commit → hash jadi ID entri → Railway Deploy Latest Commit → tes /absen live pertemuan gabungan (kedua kolom terisi + marker confirm).
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-23] Rule SKS: CDC*/AsDs* ikut master (tanpa ×) — repair skip kode itu (BELUM commit)
> **BELUM commit** — hash belum ada. Setelah commit, isi hash + SHIPPED. Railway deploy MANUAL selalu (auto-deploy off). Lanjutan entri [2026-09-23] Fix SKS sesi gabungan + entri di bawah (SKS ×sesi), timer.

- **RULE BARU (pengecualian multiplikasi ×sesi):** kode kelas **CDC\* / AsDs\*** (case-insensitive, awalan `cdc`/`asds`) → SKS = **master apa adanya, TANPA ×jumlah pertemuan**. Kode lain tetap `sks × len(nums)` utk pertemuan gabungan ("3 dan 4" → ×2). Alasan: kelas CDC/AsDs punya SKS tetap per sesi gabungan, bukan kelipatan.
- **`handlers/rekap.py` `_sks_efektif(sks, meeting, kode="")` (L785-795):** param `kode` BARU; guard awal `(kode or "").casefold().startswith(("cdc","asds"))` → `return sks or ""`. Digit guard lama tetap (meeting >=2 angka + SKS digit murni, selain itu biarkan). Call site `_build_base` L808: `_sks_efektif(c.sks, meeting, c.code)`.
- **`repair_rekap_sks_gabungan.py` SKIP CDC/AsDs:**
  - `_collect_candidates` (L68): kode casefold startswith `cdc`/`asds` → bukan kandidat (tak disentuh).
  - `_fix_sks` (L79): CDC/AsDs → `None` (master apa adanya).
  - `_selftest` +4 assert (L116-118): `"3 dan 4","2","CDC123"` → None; `"1 dan 2","4","AsDs2"` → None; lowercase `asds2` → None; kandidat list row 6-7 tak ikut.
- **`verify_zoom_picker.py` blok 7b (L305-317) +4 check:** AsDs2 `"1 dan 2"` master 4 tanpa ×2; CDC123 case-insensitive → master apa adanya; kode lain (ARCH1 `"3 dan 4"` 2→4) tetap ×sesi; single/kode kosong tetap apa adanya.
- **VERIFIKASI:** `py verify_zoom_picker.py` **26/26 PASS** (22 lama + 4 baru) + `py repair_rekap_sks_gabungan.py --selftest` **OK** + compileall OK. Offline, tanpa creds, bot TIDAK dijalankan.
- **NEXT (user):** koordinasikan jadwal nonaktif → commit → Railway Deploy Latest Commit → jalankan `py repair_rekap_sks_gabungan.py` (dry-run dulu, lalu `--write`) → tes /rekap live pertemuan gabungan (SKS ×sesi utk kode reguler, CDC/AsDs tetap master).
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-23] DATE step /zoom — personal pilih tanggal last/next (BELUM commit)
> **BELUM commit** — hash belum ada. Setelah commit, isi hash + SHIPPED. Railway deploy MANUAL selalu (auto-deploy off). Review **APPROVED** — 0 S1/S2/S3.

- **Step DATE baru di /zoom (`handlers/log.py` pick_class L348-354, pick_date L358-368):**
  - **Personal** (`category` bukan Backup/Make-up) + `last != nxt` → tawarkan **📅 Tanggal kelasnya?** via `_date_kb` (L301): tombol **"📅 {tanggal} · jadwal berikutnya"** (`d:next`, default / perilaku lama) dan **"📅 {tanggal} · pertemuan terakhir"** (`d:last`). Balik ke MEETING.
  - **last == nxt** → skip DATE, langsung MEETING (default next = sama).
  - **Backup/Make-up SKIP DATE** — tanggal eksplisit dari sheet, langsung MEETING (tanpa pertanyaan).
  - **Override disimpan:** `context.user_data["lecture_date"]` = `next_date_for_day` / `last_date_for_day` (dd/mm/yyyy string); **di-pop tiap pick_class** (reset per pilih kelas). `_class_date` (L491) = override ?? next (default). `_build_record` pakai override → tulis kolom D sheet = tanggal user pilih.
- **Dupe gate ikut override:** `_find_conflicts(kode, tanggal, pertemuan)` dipanggil dgn **lecture_date override** (bukan next default) — jalur `_meeting_warnings` (step meeting) + confirm `rec.lecture_date` (L605). Force-conflict (`force_conflict` one-shot, clear setelah save sukses) TIDAK berubah — override tetap berlaku.
- **Backup tidak geser progres** (tetap dari entri hybrid picker; tak berubah sesi ini).
- **VERIFIKASI:** `verify_log_date.py` (BARU, untracked, 18 check: DATE render last/next, skip saat equal, backup skip, pick last/next set override + return MEETING, stale → END, reset tiap pick, default next, backup eksplisit parse, build-record override, dupe-gate pakai override) + regresi **total 73/73 PASS** (verify_zoom_picker / zoom_picker_groups / zoom_display / zoom_hybrid / html_escape dkk). compileall OK. Offline tanpa creds, bot TIDAK dijalankan.
- **NEXT (user):** commit → hash jadi ID entri → Railway Deploy Latest Commit → tes /zoom live: personal pilih "pertemuan terakhir" → tanggal D sheet = last, dupe gate cek thd tanggal itu; backup tetap tanpa pertanyaan tanggal.
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway; gambar → vision agent. Cavemem MCP down — append manual.

## [2026-09-26] Backup fix: delegasi fasil-awal PPC01 + week filter Senin-Minggu WIB SHIPPED ead798d
> **SHIPPED** commit `ead798d` pushed `35ebf98..ead798d` (HEAD main = ead798d). Railway deploy MANUAL selalu (auto-deploy off) — **BELUM diklik**. Review **APPROVED**.

- **BUG 1 — PPC01 transfer (delegasi fasil-awal):** user transfer kelas PPC01 ke pengganti; picker kelas sendiri (`_fetch_classes`) TETAP menampilkan PPC01 (kelas tak hilang), dan rekaman pengganti di Zoom Record TIDAK dianggap done utk fasil-awal (A) → dobel-dorong/input ganda.
- **FIX (sheets.py):**
  - **`_active_backup_keys(facilitator_name)` (L590)** — NEW: set `(kode.casefold(), dd/mm/yyyy)` dari Backup sheet di mana nama = **FASIL AWAL (col B)**, match **exact normalize** (bukan partial — review S3: partial "Ratu" bisa match "Ratu Bilqis" salah). Arah kebalikan `_fetch_backup_classes` (yg cari col I pengganti). Return SEMUA historis by design — consumer filter minggu, butuh tanggal penuh utk `_get_done_by_date`.
  - **`_fetch_classes` (L618)** — kelas yang sedang didelegasikan (kode+tanggal minggu ini match `_active_backup_keys`) **DI-DROP** dari picker A — pengganti yang bertanggung jawab. Cek via `_class_week_date_str(day)` (last/next dalam window) lalu `(code.casefold(), dkey) in delegated`.
  - **`_get_done_by_date` (L872)** — delegasi **dianggap DONE utk A**: record masuk kalau `col C == A` ATAU `(kode, tgl) in delegated`. **Col C Zoom Record TETAP perekam** (pengganti) — attribusi tetap, tak menimpa.
- **BUG 2 — week leak backup:** `_fetch_backup_classes` pulangkan SEMUA backup cocok nama tanpa cek tanggal → backup basi ("Selasa 8 Sep") muncul di "Selasa 15 Sep" (lanjutan fix schedule [2026-09-21] `737b933d`).
- **FIX week filter (Senin..Minggu WIB, window konsisten):**
  - **`_fetch_backup_classes` (L1263)** — `week_span_wib()` (L2376) → parse `_parse_tanggal_panjang` (L2362) backup col C; di luar `[mon..sun]` → **skip**. **Fail-open:** tanggal tak ter-parse → TETAP tampil (perilaku lama dipertahankan).
  - **`backup_date_in_week(c, today)` (L2408)** — helper publik: parse backup_hari_tanggal, None/in-window → True, di luar → False; fail-open sama. Dipakai absen picker + reminder.
  - **`absen.py` L50** — `today_kodes` kini `sheets.backup_date_in_week(c) and c.day == today` (backup basi tak masuk "Hari Ini").
  - **`reminder.py` L77 + L105** — reminder filter `backup_date_in_week(c)` (kelas basi tak di-remind lintas minggu).
- **Review S3 fixed:** partial match fasil-awal → **exact** di `_active_backup_keys` (col B). Pengganti col I tetap partial (standar lama).
- **VERIFIKASI:** `verify_backup_delegation.py` (BARU, stub no-creds) **11/11 PASS** + `verify_backup_master.py` PASS. compileall OK. Bot TIDAK dijalankan.
- **COMMIT ead798d (files):** `sheets.py`, `handlers/reminder.py`, `handlers/absen.py`, `verify_backup_delegation.py`, `verify_backup_master.py`.
- **Dirty unstaged (di luar fix ini, tunggu instruksi user):** MEMORY.md (file ini), `handlers/backup.py`, `cancel.py`, `register.py`, `sinkron.py`, PDF PPC01.
- **NEXT (user):** Railway **Deploy Latest Commit** → cek ACTIVE jadi `ead798d` → tes live: fasil A /zoom → PPC01 tak muncul (sudah delegasi); pengganti log PPC01 → A tampil done; /absen + reminder backup basi tak tampil.
- **Rules tetap:** sync gspread = anti-pattern; JANGAN run lokal bareng Railway (409 Conflict); gambar → vision agent. Cavemem MCP down — append manual.
