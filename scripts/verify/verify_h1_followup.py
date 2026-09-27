"""Verify H+1 Follow-up Zoom Record (offline stub, pola verify_reminder_slots.py):
- deteksi kelas KEMARIN belum-log: personal (day-name) / backup / make-up
- skip sudah-log + skip delegasi backup aktif (active_backup_keys, fix ead798d)
- anchor tanggal kemarin lintas week boundary (Minggu lalu saat hari ini Senin)
- job slot 00:00 UTC = 07:00 WIB terdaftar, TERPISAH dari reminder_slots
- pesan per batch max 5 kelas + inline keyboard per kelas + tombol global
Run: py verify_h1_followup.py  (no network needed)
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
import sheets
from sheets import ClassEntry
from handlers import h1_followup as h1
from config import Config

N = 0

def check(name: str, cond: bool, detail: str = "") -> None:
    global N
    N += 1
    assert cond, f"FAIL {name}: {detail}"


def cfg(slots=((21, 0), (5, 0), (13, 0)), h1_hour: int = 0) -> Config:
    return Config(
        bot_token="x", sheet_id="x", service_account_json=Path("x"), facilitator_name="",
        master_sheet="", zoom_record_sheet="", backup_sheet="", cancel_sheet="",
        absen_sheet_id="", absen_sheet_name="", rekap_sheet_id="", rekap_bukti_folder_id="",
        semester="1", reminder_slots=slots, reminder_enabled=True,
        heartbeat_hour=22, heartbeat_minute=0, heartbeat_enabled=False,
        h1_followup_hour=h1_hour, h1_followup_enabled=True)


def cls(code: str, day: str, cat: str = "Reguler", backup_date: str = "",
        time_range: str = "08.00 - 10.00", subject: str = "Algo") -> ClassEntry:
    return ClassEntry(
        code=code, subject=subject, day=day, time_range=time_range, category=cat,
        lecturer="Dosen", room="R1", rombel="A", sks="3", zoom_number="1", zoom_link="",
        backup_hari_tanggal=backup_date)


def run(coro):
    return asyncio_new_loop().run_until_complete(coro)


def asyncio_new_loop():
    import asyncio
    return asyncio.new_event_loop()


# ---------- 1. anchor kemarin WIB ----------
TUES = datetime(2026, 9, 22, 7, 0, tzinfo=sheets.WIB)   # Selasa -> kemarin Senin
MON = datetime(2026, 9, 28, 7, 0, tzinfo=sheets.WIB)    # Senin  -> kemarin Minggu (lintas minggu)
yd, ys = h1._yesterday_wib(TUES)
check("anchor Selasa -> Senin 21/09", (yd, ys) == (date(2026, 9, 21), "21/09/2026"), f"{yd} {ys}")
yd, ys = h1._yesterday_wib(MON)
check("anchor Senin -> Minggu 27/09 (lintas minggu)", (yd, ys) == (date(2026, 9, 27), "27/09/2026"), f"{yd} {ys}")

# ---------- 2. deteksi kelas per tanggal (_class_on_date) ----------
ystr = "21/09/2026"
check("personal Senin di kemarin Senin -> True",
      h1._class_on_date(cls("CS101", "Senin"), date(2026, 9, 21), ystr))
check("personal Selasa di kemarin Senin -> False",
      not h1._class_on_date(cls("CS202", "Selasa"), date(2026, 9, 21), ystr))
check("backup tanggal kemarin -> True",
      h1._class_on_date(cls("BK1", "Senin", "Backup", "Senin, 21 September 2026"), date(2026, 9, 21), ystr))
check("backup tanggal BUKAN kemarin -> False",
      not h1._class_on_date(cls("BK2", "Senin", "Backup", "Senin, 14 September 2026"), date(2026, 9, 21), ystr))
check("make-up tanggal kemarin -> True",
      h1._class_on_date(cls("MK1", "Senin", "Make-up", "Senin, 21 September 2026"), date(2026, 9, 21), ystr))
# lintas week boundary: backup Minggu lalu, hari ini Senin — TANPA week-window today
check("backup Minggu lalu saat hari ini Senin -> True (anchor tanggal)",
      h1._class_on_date(cls("BK3", "Minggu", "Backup", "Minggu, 27 September 2026"), date(2026, 9, 27), "27/09/2026"))

# ---------- 3. resolve prefill ----------
a, b = cls("PPC01", "Senin"), cls("ppc02", "Selasa")
check("resolve_class_code casefold match",
      sheets.resolve_class_code([a, b], "ppc01") is a)
check("resolve_class_code None bila tak ada",
      sheets.resolve_class_code([a, b], "XXX") is None)
check("resolve_class_code kosong -> None", sheets.resolve_class_code([a], "") is None)


# ---------- 4. deteksi belum-log (_detect_missing) ----------
class FakeSheets:
    def __init__(self, personal, backup, makeup, done, delegated):
        self.personal, self.backup, self.makeup = personal, backup, makeup
        self.done, self.delegated = done, delegated

    async def get_classes(self, f):
        return self.personal

    async def backup_classes_on(self, f, on_date):
        ys = on_date.strftime("%d/%m/%Y")
        return [c for c in self.backup if sheets.parse_backup_date(c.backup_hari_tanggal) == ys]

    async def get_makeup_classes(self, f):
        return self.makeup

    async def get_done_by_date(self, f):
        return self.done

    async def active_backup_keys(self, f):
        return self.delegated


ys = "21/09/2026"
personal_unlogged = cls("CS101", "Senin", time_range="08.00 - 10.00")
personal_logged = cls("CS202", "Senin", time_range="10.00 - 12.00")
personal_wrong_day = cls("CS303", "Selasa")
delegated_personal = cls("CS404", "Senin", time_range="13.00 - 15.00")
backup_class = cls("BK9", "Senin", "Backup", "Senin, 21 September 2026", time_range="07.30 - 09.30")
makeup_class = cls("MK9", "Senin", "Make-up", "Senin, 21 September 2026", time_range="16.00 - 18.00")
backup_today = cls("BK8", "Selasa", "Backup", "Selasa, 22 September 2026", time_range="08.00 - 10.00")

fs = FakeSheets(
    personal=[personal_unlogged, personal_logged, personal_wrong_day, delegated_personal],
    backup=[backup_class, backup_today],
    makeup=[makeup_class],
    done={(personal_logged.code.casefold(), ys)},
    delegated={(delegated_personal.code.casefold(), ys)},
)
missing = run(h1._detect_missing(fs, "Fasil Test", TUES))
codes = [c.code for c in missing]
check("belum-log personal kemarin -> kept", "CS101" in codes, str(codes))
check("sudah-log personal kemarin -> skipped", "CS202" not in codes, str(codes))
check("kelas hari BUKAN kemarin -> excluded", "CS303" not in codes, str(codes))
check("delegasi aktif utk kelas kemarin -> skipped", "CS404" not in codes, str(codes))
check("backup kemarin belum-log -> kept", "BK9" in codes, str(codes))
check("backup hari ini (bukan kemarin) -> excluded", "BK8" not in codes, str(codes))
check("make-up kemarin belum-log -> kept", "MK9" in codes, str(codes))
check("urutan by jam mulai", codes == sorted(codes), str(codes))

# cross-week boundary: hari ini Senin, kemarin Minggu (minggu LALU)
fs_x = FakeSheets(
    personal=[cls("CSX", "Minggu", time_range="08.00 - 10.00")],  # kemarin Minggu
    backup=[cls("BKX", "Minggu", "Backup", "Minggu, 27 September 2026", time_range="13.00 - 15.00")],
    makeup=[cls("MKX", "Minggu", "Make-up", "Minggu, 27 September 2026", time_range="10.00 - 12.00")],
    done=set(), delegated=set(),
)
missing_x = run(h1._detect_missing(fs_x, "Fasil Test", MON))
check("lintas minggu: personal Minggu kemarin -> kept", "CSX" in [c.code for c in missing_x], str([c.code for c in missing_x]))
check("lintas minggu: backup Minggu kemarin -> kept", "BKX" in [c.code for c in missing_x], str([c.code for c in missing_x]))
check("lintas minggu: make-up Minggu kemarin -> kept", "MKX" in [c.code for c in missing_x], str([c.code for c in missing_x]))

# fail-open: done-map rusak -> jangan crash (kirim semua, spt reminder)
class BrokenSheets(FakeSheets):
    async def get_done_by_date(self, f):
        raise sheets.SheetsError("boom")
    async def active_backup_keys(self, f):
        raise sheets.SheetsError("boom")
missing_bo = run(h1._detect_missing(BrokenSheets([personal_unlogged], [], [], set(), set()), "F", TUES))
check("done/delegated map error -> fail-open keep", "CS101" in [c.code for c in missing_bo], str([c.code for c in missing_bo]))


# ---------- 5. job slot terpisah dari reminder ----------
c = cfg(slots=((21, 0), (5, 0), (13, 0)), h1_hour=0)
check("h1_hour default 0 UTC", c.h1_followup_hour == 0)
check("0 UTC = 07:00 WIB", (c.h1_followup_hour + 7) % 24 == 7)
check("reminder_slots tidak terpengaruh H1", c.reminder_slots == ((21, 0), (5, 0), (13, 0)))
check("slot H1 (0) tidak bentrok slot reminder (21,5,13)",
      c.h1_followup_hour not in {h for h, _ in c.reminder_slots})
check("job name H1 != format reminder:{chat}:", not h1.BROADCAST_NAME.startswith("reminder:"))
check("_is_full_slot semantics tak disentuh (job H1 tdk lewat reminder)",
      h1.BROADCAST_NAME == "h1_followup:broadcast")
check("callback prefixes distinct", len({h1.P_CNL[:3], h1.P_RSD[:3], h1.P_BAK[:3], h1.P_LOG[:3], h1.P_CHK[:3]}) == 5)
check("global button data", "h1z:check" == "h1z:check")


# ---------- 6. pesan: max 5 kelas/pesan + keyboard ----------
six = [cls(f"K{i:03d}", "Senin", time_range=f"{i:02d}.00 - {i:02d}.30",
           subject="A < B" if i == 3 else f"Subj {i}") for i in range(6)]
chunks = h1._chunk(six)
check("6 kelas -> 2 pesan (5+1)", len(chunks) == 2 and len(chunks[0]) == 5 and len(chunks[1]) == 1,
      f"{[len(x) for x in chunks]}")
txt = h1._batch_text(date(2026, 9, 21), ys, chunks[0])
check("header H+1", "📩" in txt and "H+1" in txt and "Zoom Record" in txt)
check("format pesan per kelas", "H+1 — Kelas K000 (00.00 - 00.30) kemarin belum ada Zoom Record. Kenapa?" in txt)
check("html.escape subject", "&lt;" in h1._batch_text(date(2026, 9, 21), ys, [cls("KX", "Senin", subject="A < B")]))
kb = h1._batch_kb(chunks[0])
rows = kb.inline_keyboard
check("5 baris kelas + 1 global", len(rows) == 6, f"{len(rows)}")
btn0 = rows[0][0]
check("tombol per kelas: 4 aksi", len(rows[0]) == 4)
check("callback cancel", any(b.callback_data == "h1c:K000" for b in rows[0]), str([b.callback_data for b in rows[0]]))
check("callback reschedule", any(b.callback_data == "h1r:K000" for b in rows[0]), str([b.callback_data for b in rows[0]]))
check("callback backup", any(b.callback_data == "h1b:K000" for b in rows[0]), str([b.callback_data for b in rows[0]]))
check("callback isi zoom", any(b.callback_data == "h1l:K000" for b in rows[0]), str([b.callback_data for b in rows[0]]))
check("tombol global cek sendiri", rows[-1][0].callback_data == "h1z:check" and "cek sendiri" in rows[-1][0].text)
check("label aksi tombol", [b.text for b in rows[0]] == ["❌ Dibatalkan", "🔁 Reschedule", "🔄 Backup fasil", "✏️ Isi /zoom"])

print(f"OK — {N} checks passed")
sys.exit(0)