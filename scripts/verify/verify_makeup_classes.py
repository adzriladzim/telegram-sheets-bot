"""Offline verification stub — kelas MAKE-UP dari tab Cancel (READ-ONLY).

Checks (no creds, gspread stubbed):
  1. _fetch_makeup_classes filter: B kosong / J='true' / L kosong / N tidak match -> skip.
  2. Mapping ClassEntry: kode=D, subject=C, lecturer=B, jam=M, room=Q, zoom O/P,
     ket=R, backup_hari_tanggal=L (tanggal eksplisit), sks=H, category='Make-up'.
  3. day dari L -> 'Rabu, 9 September 2026' -> 'Rabu'.
  4. Semester via master join: rombel-match prefer Q=room, fallback pertama.
  5. _fetch_makeup_notes: kode -> (L, N) untuk I match (baris asli kelas user di-cancel).
  6. get_all_loggable_classes return TUPLE 3 (personal, backup, makeup).
  7. Semua call-site handler unpack 3-tuple (guards regresi unpack crash).
  8. Zoom-record path make-up: log._build_record tanggal = L (bukan next_date_for_day).
Run:  py verify_makeup_classes.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
import sheets
from config import Config

SS_MAIN = "ss_main"
PASS, FAIL = [], []


def check(label, cond, msg=""):
    (PASS if cond else FAIL).append((label, msg))
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))


class FakeWorksheet:
    def __init__(self, title, rows):
        self.title, self.rows = title, rows
        self.row_count, self.col_count = 1000, 26

    def get_all_values(self):
        return [list(r) for r in self.rows]


class FakeSpreadsheet:
    def __init__(self, ss_id, worksheets):
        self.id = ss_id
        self._ws = {w.title: w for w in worksheets}

    def worksheet(self, title):
        return self._ws[title]


class FakeClient:
    def __init__(self, spreadsheets):
        self.spreadsheets = spreadsheets

    def open_by_key(self, ss_id):
        return self.spreadsheets[ss_id]


def fresh_client(ss_main) -> sheets.SheetsClient:
    cfg = Config(
        bot_token="x", sheet_id=SS_MAIN, service_account_json=Path("secrets/none.json"),
        facilitator_name="", master_sheet="Master", zoom_record_sheet="Zoom Record",
        backup_sheet="Backup", cancel_sheet="Cancel", absen_sheet_id="ss_absen",
        absen_sheet_name="Absen", rekap_sheet_id="ss_rekap", rekap_bukti_folder_id="",
        semester="1", reminder_slots=((21, 0),), reminder_enabled=False,
        heartbeat_hour=22, heartbeat_minute=0, heartbeat_enabled=False,
    )
    c = sheets.SheetsClient(cfg)
    c._gc = FakeClient({SS_MAIN: ss_main})
    sheets._rows_cache.clear()
    sheets._tabs_cache.clear()
    return c


def master_row(kode, mk, dosen, rombel, sks, zoom_no, ket=""):
    r = [""] * 16
    r[0], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11], r[12] = (
        "Senin", kode, mk, dosen, rombel, rombel, sks, zoom_no, "https://meet/x", ket)
    return r


# Cancel cols (live header): A No B Dosen C Matkul D Kode E Sesi F JadwalAwal
# G Jam H SKS I Fasil(cancel) J Status(T/F) K kosong L Jadwal Make-up M Jam
# N Fasil(make-up) O Zoom(nomor) P Zoom(link) Q Room R Ket.
def cancel_row(dosen, matkul, kode, fasil, status, jadwal_makeup, jam_makeup,
               fasil_makeup, zoom_no="", zoom_link="", room="", ket="", sks="2"):
    r = [""] * 18
    r[1], r[2], r[3], r[7], r[8], r[9], r[11], r[12], r[13] = (
        dosen, matkul, kode, sks, fasil, status, jadwal_makeup, jam_makeup, fasil_makeup)
    r[14], r[15], r[16], r[17] = zoom_no, zoom_link, room, ket
    return r


MASTER = [
    ["Header"] * 16,
    master_row("CS101", "Algo", "Dosen X", "3 Ilkom Pro", "3", "28"),   # rombel A
    master_row("CS101", "Algo", "Dosen X", "5 Ilkom Pro", "3", "40"),   # rombel B
    master_row("CS202", "Basis Data", "Dosen Y", "4 Ilkom Pro", "3", "28", "Zoom 40"),
]
CANCEL = [
    ["header"] * 18,
    cancel_row("Dosen X", "Algo", "CS101", "Fasil A", "F", "Rabu, 9 September 2026",
               "08.00 - 10.00", "Fasil B", "33", "https://meet/z", "R.204", "makeup ket 77", "3"),
    cancel_row("Dosen Z", "Statistika", "CS303", "Fasil X", "T", "Kamis, 10 September 2026",
               "10.00 - 12.00", "Fasil B", "11", "", "R.101", ""),          # J='true' -> skip
    cancel_row("", "Matkul Kosong", "CS404", "Fasil X", "F", "Jumat, 11 September 2026",
               "10.00 - 12.00", "Fasil B"),                                   # B kosong -> skip
    cancel_row("Dosen W", "Etika", "CS505", "Fasil X", "F", "",
               "10.00 - 12.00", "Fasil B"),                                   # L kosong -> skip
    cancel_row("Dosen V", "Agama", "CS606", "Fasil A", "F", "Sabtu, 12 September 2026",
               "10.00 - 12.00", "Fasil C"),                                   # N tidak match -> skip
    cancel_row("Dosen U", "Kewarganegaraan", "CS101", "Fasil B", "F", "Rabu, 9 September 2026",
               "08.00 - 10.00", "Fasil B", "", "", "5 Ilkom Pro", "", "1"),  # master rombel-match -> sem 5
    cancel_row("Dosen T", "Statistik", "CS202", "Fasil B", "F", "Rabu, 9 September 2026",
               "08.00 - 10.00", "Fasil B", "", "", "9 Ilkom Pro", "", "3"),   # no rombel match -> sem 4 (first)
]

ss = FakeSpreadsheet(SS_MAIN, [FakeWorksheet("Master", MASTER), FakeWorksheet("Cancel", CANCEL)])

# ---------- 1. filter logic + mapping ----------
c = fresh_client(ss)
out = c._fetch_makeup_classes("Fasil B")
by_row = {e.row_index: e for e in out}
check("1. filter: hanya 3 baris valid (skip J=T/'true', B kosong, L kosong, N beda)",
      sorted(by_row) == [1, 6, 7], f"got rows {sorted(by_row)}")
e1 = by_row[1]
check("2a. mapping kode=D subject=C lecturer=B jam=M sks=H",
      e1.code == "CS101" and e1.subject == "Algo" and e1.lecturer == "Dosen X"
      and e1.time_range == "08.00 - 10.00" and e1.sks == "3", repr(e1))
check("2b. zoom O/P + room Q + ket R + category",
      e1.zoom_number == "33" and e1.zoom_link == "https://meet/z" and e1.room == "R.204"
      and e1.keterangan == "makeup ket 77" and e1.category == "Make-up", repr(e1))
check("2c. backup_hari_tanggal = L tanggal eksplisit", e1.backup_hari_tanggal == "Rabu, 9 September 2026")
check("3. day dari L -> Rabu", e1.day == "Rabu")
check("2d. zoom_label dari Nomor Zoom", e1.zoom_label == "Zoom 33")

# ---------- 4. semester via master join ----------
e_sem = by_row[6]
check("4a. master rombel-match (Q=room) -> semester 5", e_sem.semester == "5", e_sem.semester)
e_fb = by_row[7]
check("4b. no rombel match -> fallback master pertama -> semester 4", e_fb.semester == "4", e_fb.semester)

# ---------- 5. makeup notes (I match, L terisi) ----------
notes = c._fetch_makeup_notes("Fasil B")
check("5a. I=match & L terisi -> kode -> (L, N)",
      notes.get("cs101") == ("Rabu, 9 September 2026", "Fasil B"), str(notes))
check("5b. I tidak match (Fasil A) -> tidak masuk notes", "cs606" not in notes and "cs303" not in notes,
      str(notes))
# Row 6-7: I=Fasil B, L terisi -> cs101/cs202 ter-overwrite/terisi dengan N=Fasil B
check("5c. row I=Fasil B juga masuk notes", notes.get("cs202") == ("Rabu, 9 September 2026", "Fasil B"))


# ---------- 6. tuple 3 di get_all_loggable_classes ----------
class Cls3(sheets.SheetsClient):
    async def get_classes(self, facilitator_name=None):
        return [mup_p]
    async def get_backup_classes(self, facilitator_name):
        return [mup_b]
    async def get_makeup_classes(self, facilitator_name):
        return [mup_m]


mup_p = sheets.ClassEntry(code="P", subject="", day="", time_range="", category="Reguler",
                          lecturer="", room="", rombel="", sks="", zoom_number="", zoom_link="")
mup_b = sheets.ClassEntry(code="B", subject="", day="", time_range="", category="Backup",
                          lecturer="", room="", rombel="", sks="", zoom_number="", zoom_link="")
mup_m = sheets.ClassEntry(code="M", subject="", day="", time_range="", category="Make-up",
                          lecturer="", room="", rombel="", sks="", zoom_number="", zoom_link="")
c3 = Cls3(c.cfg)
c3._gc = c._gc
res = asyncio.run(c3.get_all_loggable_classes("Fasil B"))
check("6. get_all_loggable_classes -> tuple 3 (personal, backup, makeup)",
      isinstance(res, tuple) and len(res) == 3
      and [x.code for slot in res for x in slot] == ["P", "B", "M"], str(res))

# ---------- 7. call-site unpack 3-tuple ----------
HANDLERS = {
    "handlers/log.py": "personal, backup, makeup = ",
    "handlers/rekap.py": "personal, backup, makeup = ",
    "handlers/absen.py": "personal, backup, makeup = ",
    "handlers/schedule.py": "personal, backup, makeup = ",
    "handlers/stats.py": "personal, backup, makeup = ",
    "handlers/reminder.py": "personal, backup, makeup = ",
}
for path, needle in HANDLERS.items():
    src = Path(path).read_text(encoding="utf-8")
    check(f"7. call-site {path} unpack 3-tuple", needle in src)

# ---------- 8. zoom-record path make-up ----------
from handlers.log import _build_record

mup = sheets.ClassEntry(
    code="CS101", subject="Algo", day="Rabu", time_range="08.00 - 10.00", category="Make-up",
    lecturer="Dosen X", room="R.204", rombel="", sks="3", zoom_number="33", zoom_link="",
    keterangan="", backup_hari_tanggal="Rabu, 9 September 2026", semester="5",
)

class FakeCfg:
    facilitator_name = "Fasil B"
    semester = "1"


class FakeCtx:
    def __init__(self):
        self.user_data = {"cls": mup, "facilitator": "Fasil B", "meeting": "1",
                          "scheme": "Online", "zoom": "", "notes": ""}
        self.bot_data = {"cfg": FakeCfg()}


rec = _build_record(FakeCtx())
check("8a. make-up lecture_date = parse(L) = 09/09/2026",
      rec.lecture_date == "09/09/2026", rec.lecture_date)
check("8b. make-up record: kode/sks/mulai/tipe dari kolom make-up",
      rec.code == "CS101" and rec.sks == "3" and rec.start_time == "08.00"
      and rec.tipe_kelas in ("Regular", "Make-up"), rec.tipe_kelas)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for lbl, msg in FAIL:
    print(f"  FAILED: {lbl} {msg}")
raise SystemExit(1 if FAIL else 0)