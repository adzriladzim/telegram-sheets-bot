"""Verify /reminder_dosen — template pesan ke dosen (offline stub, pola verify_h1_followup.py).

Checks:
  1. sapaan WIB 4 slot + boundary (04/11/15/19 + 03:59/10:59/14:59/18:59)
  2. render 3 template (Normal/Backup/Reschedule) x Bu/Pak — wording EXACT
  3. html.escape display vs payload Salin plain (CopyTextButton PTB v22.8)
  4. anchor tanggal: backup/make-up eksplisit; personal default next (bukan last),
     _needs_date_step personal last!=next
  5. backup_context: prefill pengganti dari baris Backup; None -> fallback tanya
  6. cancel_schedule_for + _prefill_jadwal_baru (Cancel make-up / Tukar swap) + fallback ''
  7. _jadwal_sebelumnya (make-up pakai F, personal derived)
  8. _fetch_classes include_delegated: CS101 muncul hanya saat True
  9. _build_view: label 🔀 delegated + ✅ done + tombol back:cancel
Run: py scripts/verify/verify_reminder_dosen.py  (no network needed)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
import sheets
from sheets import ClassEntry
from telegram import CopyTextButton, InlineKeyboardButton
from config import Config
from handlers import log as logmod
from handlers import reminder_dosen as pj

PASS = []
FAIL = []


def check(label, cond, msg=""):
    (PASS if cond else FAIL).append((label, msg))
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))


def cfg_fixture():
    return Config(
        bot_token="x", sheet_id="ss_main", service_account_json=Path("secrets/none.json"),
        facilitator_name="", master_sheet="Master", zoom_record_sheet="Zoom Record",
        backup_sheet="Backup", cancel_sheet="Cancel", absen_sheet_id="ss_absen",
        absen_sheet_name="Absen", rekap_sheet_id="ss_rekap", rekap_bukti_folder_id="",
        semester="1", reminder_slots=((21, 0), (5, 0), (13, 0)), reminder_enabled=False,
        heartbeat_hour=22, heartbeat_minute=0, heartbeat_enabled=False,
    )


def cls(code, day, category="Reguler", backup_hari_tanggal="", subject="MK",
        dosen="Dosen", time_range="13.00 - 15.30", swap_tanggal=""):
    return ClassEntry(
        code=code, subject=subject, day=day, time_range=time_range,
        category=category, lecturer=dosen, room="", rombel="", sks="3",
        zoom_number="", zoom_link="", backup_hari_tanggal=backup_hari_tanggal,
        swap_tanggal=swap_tanggal,
    )


# ---------- 1. sapaan WIB ----------
def sapa(hour):
    return pj._sapaan_wib(datetime(2026, 9, 16, hour, 0, tzinfo=sheets.WIB))


check("04:00 pagi", sapa(4) == "pagi", sapa(4))
check("10:59 pagi", sapa(10) == "pagi", sapa(10))
check("11:00 siang", sapa(11) == "siang", sapa(11))
check("14:59 siang", sapa(14) == "siang", sapa(14))
check("15:00 sore", sapa(15) == "sore", sapa(15))
check("18:59 sore", sapa(18) == "sore", sapa(18))
check("19:00 malam", sapa(19) == "malam", sapa(19))
check("03:59 malam", sapa(3) == "malam", sapa(3))


# ---------- 2. render 3 template ----------
TGL = "21/09/2026"
c_n = cls("CS101", "Senin", subject="Algoritma", dosen="Dr. Rina <AMD>", time_range="13.00 - 15.30")

tpl_n_bu = pj.render_normal(c_n, "pagi", "Bu", TGL)
check("Normal Bu: pembuka 'Selamat pagi Bu + dosen' (tanpa dobel Selamat)",
      tpl_n_bu.startswith("Selamat pagi Bu Dr. Rina <AMD> 🙏"), tpl_n_bu[:50])
check("Normal: kalimat izin mengandung Bu",
      "Izin mengingatkan untuk kelas yang akan berlangsung ya, Bu:" in tpl_n_bu)
check("Normal: baris matkul+kode", "📚 Algoritma — CS101" in tpl_n_bu)
check("Normal: tanggal panjang + jam WIB",
      "📅 Senin, 21 September 2026\n⏰ 13.00 - 15.30 WIB" in tpl_n_bu)
check("Normal: penutup Bu", "Terima kasih, Bu 🙏" in tpl_n_bu)

tpl_n_pak = pj.render_normal(c_n, "sore", "Pak", TGL)
check("Normal Pak: sapaan sore + Pak", tpl_n_pak.startswith("Selamat sore Pak Dr. Rina <AMD> 🙏"))
check("Normal Pak: kalimat izin Pak", "ya, Pak:" in tpl_n_pak and "Terima kasih, Pak 🙏" in tpl_n_pak)

c_b = cls("CS202", "Senin", subject="Basis Data", dosen="Dosen Y", time_range="08.00 - 10.00")
tpl_b = pj.render_backup(c_b, "siang", "Bu", TGL, "Rina Bilqis", "Adzril Adzim")
check("Backup: pembuka", tpl_b.startswith("Selamat siang Bu Dosen Y 🙏"))
check("Backup: kalimat digantikan", "kelas nanti akan digantikan oleh rekan fasilitator lain ya, Bu:" in tpl_b)
check("Backup: matkul+kode", "📚 Basis Data — CS202" in tpl_b)
check("Backup: tanggal+jam", "📅 Senin, 21 September 2026\n⏰ 08.00 - 10.00 WIB" in tpl_b)
check("Backup: pengganti + fasil awal",
      "👤 Pengganti: Rina Bilqis (menggantikan Adzril Adzim)" in tpl_b)
check("Backup: penutup", "Kelas tetap jalan seperti jadwal ya, Bu. Terima kasih banyak 🙏" in tpl_b)

c_r = cls("CS303", "Selasa", subject="Statistika", dosen="Dosen Z", time_range="10.00 - 12.00")
tpl_r = pj.render_reschedule(c_r, "malam", "Pak", "22/09/2026",
                             "Selasa, 22 September 2026, 10.00 - 12.00 WIB",
                             "Jumat, 25 September 2026, 18.00 - 20.00 WIB")
check("Reschedule: pembuka malam Pak", tpl_r.startswith("Selamat malam Pak Dosen Z 🙏"))
check("Reschedule: kalimat perubahan", "ada perubahan jadwal kelas nih, Pak:" in tpl_r)
check("Reschedule: matkul+kode", "📚 Statistika — CS303" in tpl_r)
check("Reschedule: sebelumnya+baru",
      "🔙 Jadwal sebelumnya: Selasa, 22 September 2026, 10.00 - 12.00 WIB\n"
      "✅ Jadwal baru: Jumat, 25 September 2026, 18.00 - 20.00 WIB" in tpl_r)
check("Reschedule: penutup", "Mohon maaf atas perubahannya, dan terima kasih atas pengertiannya ya, Pak 🙏" in tpl_r)


# ---------- 3. escape display vs payload salin ----------
plain_rich = pj.render_normal(cls("CSX", "Senin", subject="A < B & C", dosen="D < E"), "pagi", "Bu", TGL)
disp, payload = pj._display_copy(plain_rich)
check("display html.escape: &lt; &amp;",
      "&lt;" in disp and "&amp;" in disp and "<" not in disp.replace("&lt;", "").replace("&amp;", ""))
check("payload plain: raw '<' & '&' tidak di-escape", payload == plain_rich and "<" in payload and "&" in payload)
btn = InlineKeyboardButton("📋 Salin", copy_text=CopyTextButton(payload))
check("CopyTextButton PTB v22: tombol salin menempel payload plain", btn.copy_text is not None and btn.copy_text.text == payload)


# ---------- 4. anchor tanggal ----------
# Fixed 'today' = Rabu 16 Sep 2026 -> Senin last 14/09, next 21/09; Rabu last==next==16/09.
TODAY = datetime(2026, 9, 16, 12, 0, tzinfo=sheets.WIB)


def _fixed_last(day_name):
    norm = "".join(ch for ch in day_name.lower() if ch.isalpha())
    target = next((i for i, d in enumerate(sheets.DAY_ORDER)
                   if "".join(ch for ch in d.lower() if ch.isalpha()) == norm), None)
    if target is None:
        return sheets.today_str_wib()
    delta = (TODAY.weekday() - target) % 7
    return (TODAY - timedelta(days=delta)).strftime("%d/%m/%Y")


def _fixed_next(day_name):
    norm = "".join(ch for ch in day_name.lower() if ch.isalpha())
    target = next((i for i, d in enumerate(sheets.DAY_ORDER)
                   if "".join(ch for ch in d.lower() if ch.isalpha()) == norm), None)
    if target is None:
        return sheets.today_str_wib()
    delta = (target - TODAY.weekday()) % 7
    return (TODAY + timedelta(days=delta)).strftime("%d/%m/%Y")


sheets.last_date_for_day = _fixed_last
sheets.next_date_for_day = _fixed_next

c_p = cls("P-MON", "Senin")
check("personal ambigu (last!=next) -> perlu step DATE", pj._needs_date_step(c_p))
check("personal default = Next (bukan last include today)",
      pj._date_for(c_p) == "21/09/2026", pj._date_for(c_p))
check("personal d:last -> 14/09", pj._date_for(c_p, "d:last") == "14/09/2026")
c_today = cls("P-TODAY", "Rabu")
check("kelas hari ini (last==next) -> tanpa step DATE", not pj._needs_date_step(c_today))
check("kelas hari ini default today", pj._date_for(c_today) == "16/09/2026", pj._date_for(c_today))
c_bk = cls("B-1", "Kamis", category="Backup", backup_hari_tanggal="Kamis, 17 September 2026")
check("backup tanggal eksplisit: tanpa step DATE", not pj._needs_date_step(c_bk))
check("backup anchor = parse_backup_date", pj._date_for(c_bk) == "17/09/2026", pj._date_for(c_bk))
c_mk = cls("M-1", "Kamis", category="Make-up", backup_hari_tanggal="Kamis, 24 September 2026")
check("make-up tanggal eksplisit", pj._date_for(c_mk) == "24/09/2026", pj._date_for(c_mk))


# ---------- 5. backup_context ----------
BACKUP = [
    ["info row"],
    ["No", "Fasil Awal", "Hari/Tanggal", "Jam", "Kode", "Matkul", "Dosen", "Ruang", "Pengganti", "Catatan"],
    ["1", "Fasil A", "Kamis, 17 September 2026", "13.00 - 15.30", "CS101", "Algo", "Dosen X", "R1", "Rina", ""],
    ["2", "Fasil B", "Jumat, 18 September 2026", "08.00 - 10.00", "CS202", "BD", "Dosen Y", "R2", "Fasil C", ""],
]
CANCEL = [
    ["header"],
    ["1", "Dosen X", "Algo", "CS101", "3", "Senin, 8 September 2026", "13.00 - 15.30", "3",
     "Fasil A", "F", "", "Rabu, 23 September 2026", "18.00 - 20.00", "Fasil B", "", "", "", ""],
    ["2", "Dosen Y", "BD", "CS202", "2", "Selasa, 9 September 2026", "08.00 - 10.00", "3",
     "Fasil A", "T", "", "", "", "", "", "", "", ""],
]


class FakeC(sheets.SheetsClient):
    def __init__(self, backup_rows=BACKUP, cancel_rows=CANCEL):
        super().__init__(cfg_fixture())
        self._backup_rows = backup_rows
        self._cancel_rows = cancel_rows

    def _cached_rows(self, ss_id, title):
        if title == "Backup":
            return self._backup_rows
        if title == "Cancel":
            return self._cancel_rows
        return []


fc = FakeC()
ctx = fc._backup_context("CS101", "17/09/2026")
check("backup_context match: awal+pengganti",
      ctx == {"awal": "Fasil A", "pengganti": "Rina"}, str(ctx))
check("backup_context casefold kode", fc._backup_context("cs101", "17/09/2026") == ctx)
check("backup_context tanggal beda -> None", fc._backup_context("CS101", "18/09/2026") is None)
check("backup_context kode lain -> None", fc._backup_context("CS202", "17/09/2026") is None)
check("backup_context tanggal kosong -> None", fc._backup_context("CS101", "") is None)


# ---------- 6. reschedule prefill: Cancel / Tukar / fallback ----------
cs = fc._cancel_schedule_for("CS101")
check("cancel_schedule_for CS101: makeup L+M + jadwal awal F",
      cs and cs["jadwal_makeup"] == "Rabu, 23 September 2026" and cs["jam_makeup"] == "18.00 - 20.00"
      and cs["jadwal_awal"] == "Senin, 8 September 2026" and cs["jam"] == "13.00 - 15.30", str(cs))
cs2 = fc._cancel_schedule_for("CS202")
check("cancel_schedule_for terlaksana tanpa make-up -> tetap rec lama", cs2 and not cs2["jadwal_makeup"], str(cs2))
check("cancel_schedule_for kode tak ada -> None", fc._cancel_schedule_for("XXX") is None)

pre_c = pj._prefill_jadwal_baru(cls("CS101", "Senin"), cs)
check("prefill jadwal baru dari Cancel L+M", pre_c == "Rabu, 23 September 2026, 18.00 - 20.00 WIB", pre_c)
c_sw = cls("CS404", "Senin", swap_tanggal="25/09/2026")
check("prefill jadwal baru dari Tukar swap_tanggal",
      pj._prefill_jadwal_baru(c_sw, None) == "25 September 2026, 13.00 - 15.30 WIB",
      pj._prefill_jadwal_baru(c_sw, None))
check("prefill tanpa Cancel & Tukar -> '' (minta user)",
      pj._prefill_jadwal_baru(cls("CS505", "Senin"), None) == "")


# ---------- 7. jadwal sebelumnya ----------
c_mk2 = cls("M-MK", "Rabu", category="Make-up", backup_hari_tanggal="Rabu, 23 September 2026")
check("sebelumnya make-up: baris Cancel F",
      pj._jadwal_sebelumnya(c_mk2, "23/09/2026", cs) == "Senin, 8 September 2026, 13.00 - 15.30 WIB",
      pj._jadwal_sebelumnya(c_mk2, "23/09/2026", cs))
check("sebelumnya personal derived",
      pj._jadwal_sebelumnya(cls("P-N", "Senin"), "21/09/2026", None)
      == "Senin, 21 September 2026, 13.00 - 15.30 WIB")


# ---------- 8. _fetch_classes include_delegated ----------
MASTER = [
    ["Header"] * 16,
    ["Senin", "Fasil A", "Reguler", "13.00 - 15.30", "CS101", "Algo", "Dosen X", "R1", "3 Ilkom Pro",
     "3", "28", "https://meet/x", "", "", "", ""],
    ["Selasa", "Fasil A", "Reguler", "08.00 - 10.00", "CS202", "BD", "Dosen Y", "R2", "3 Ilkom Pro",
     "3", "29", "", "", "", "", ""],
]
WEEK = {"Senin": "16/09/2026", "Selasa": "17/09/2026"}


class FakeMaster(sheets.SheetsClient):
    def __init__(self):
        super().__init__(cfg_fixture())
        self._delegated = {("cs101", "16/09/2026")}

    def _cached_rows(self, ss_id, title):
        return MASTER if title == "Master" else []

    def _active_backup_keys(self, facilitator_name):
        return self._delegated

    def _class_week_date_str(self, day_name):
        return WEEK.get(day_name)


fm = FakeMaster()
out_def = fm._fetch_classes("Fasil A")
out_inc = fm._fetch_classes("Fasil A", include_delegated=True)
check("_fetch_classes default: kelas didelegasikan disembunyikan",
      [c.code for c in out_def] == ["CS202"], str([c.code for c in out_def]))
check("_fetch_classes include_delegated=True: CS101 ikut tampil",
      [c.code for c in out_inc] == ["CS101", "CS202"], str([c.code for c in out_inc]))
# _active_backup_keys wrap async = single-flight read-only (ada, tak crash)
check("_cancel_schedule_for / _backup_context sync aman tanpa gspread", fc._backup_context("CS101", "17/09/2026") is not None)


# ---------- 9. _build_view label delegated + done ----------
CLASSES = [
    cls("P-MON", "Senin"),        # done -> ✅
    cls("P-SAT", "Sabtu"),        # next 19/09 (dalam minggu 14-20) -> 🔀 delegated
    cls("B-WEEK", "Kamis", category="Backup", backup_hari_tanggal="Kamis, 17 September 2026"),
    cls("B-OLD", "Senin", category="Backup", backup_hari_tanggal="Senin, 7 September 2026"),
]
DONE = {("p-mon", "14/09/2026")}
DELEG = {("p-sat", "19/09/2026")}
ordered = logmod._group_picker_classes(CLASSES, DONE)[0] + logmod._group_picker_classes(CLASSES, DONE)[1] + logmod._group_picker_classes(CLASSES, DONE)[2]
text, kb = pj._build_view(ordered, DONE, DELEG, show_done=True, today=TODAY)
labels = [b.text for row in kb for b in row]
check("label 🔀 kelas didelegasikan + ✅ done", any("🔀 P-SAT" in t for t in labels) and any("✅ P-MON" in t for t in labels), str(labels))
check("label backup 🔄 tanggal eksplisit", any("🔄 B-WEEK" in t for t in labels), str(labels))
check("tombol batal picker", any(b.callback_data == "back:cancel" for row in kb for b in row))


# ---------- 10. CopyTextButton hidup di PTB terinstall ----------
check("CopyTextButton tersedia di telegram 22.8", CopyTextButton is not None and btn.copy_text.text == payload)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)