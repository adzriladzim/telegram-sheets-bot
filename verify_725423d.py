"""Offline verification stub — HEAD 725423d. No live creds; gspread fully stubbed.

Checks:
  1. values_batch_update write ranges are worksheet-qualified ('Title'!A1)
  2. empty/short rows don't IndexError in absen read path (5e7e9ed guards)
  3. multi-blok absen aggregation across tabs (3bc1549)
  4. _guard_grid raises SheetsError past final grid bounds; full-tab append fails clean
Run: py verify_725423d.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import sheets
from config import Config

SS_MAIN, SS_ABSEN, SS_REKAP = "ss_main", "ss_absen", "ss_rekap"
PASS = []  # (label, msg)
FAIL = []


def check(label, cond, msg=""):
    (PASS if cond else FAIL).append((label, msg))
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))


class FakeClient:
    def __init__(self, spreadsheets):
        self.spreadsheets = spreadsheets

    def open_by_key(self, ss_id):
        return self.spreadsheets[ss_id]


class FakeWorksheet:
    def __init__(self, title, rows, row_count=1000, col_count=26, spreadsheet=None):
        self.title = title
        self.rows = rows
        self.row_count = row_count
        self.col_count = col_count
        self.spreadsheet = spreadsheet
        self.updates = []

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def update(self, rng, values, value_input_option="USER_ENTERED"):
        self.updates.append((rng, values, value_input_option))


class FakeSpreadsheet:
    def __init__(self, ss_id, worksheets):
        self.id = ss_id
        self._ws = {w.title: w for w in worksheets}
        for w in worksheets:
            w.spreadsheet = self
        self.batch_updates = []
        self.batch_gets = []

    def worksheet(self, title):
        return self._ws[title]

    def worksheets(self):
        return list(self._ws.values())

    def values_batch_update(self, payload):
        self.batch_updates.append(payload)

    def values_batch_get(self, ranges):
        self.batch_gets.append(ranges)
        out = []
        for rng in ranges:
            m = re.match(r"'([^']+)'", rng)
            out.append({"values": self._ws[m.group(1)].get_all_values()})
        return {"valueRanges": out}


def fresh_client():
    cfg = Config(
        bot_token="x", sheet_id=SS_MAIN, service_account_json=Path("secrets/none.json"),
        facilitator_name="", master_sheet="Master", zoom_record_sheet="Zoom Record",
        backup_sheet="Backup", cancel_sheet="Cancel", absen_sheet_id=SS_ABSEN,
        absen_sheet_name="Absen", rekap_sheet_id=SS_REKAP, rekap_bukti_folder_id="",
        semester="1", reminder_hour=11, reminder_minute=0, reminder_enabled=False,
        heartbeat_hour=22, heartbeat_minute=0, heartbeat_enabled=False,
    )
    gc = FakeClient({})
    c = sheets.SheetsClient(cfg)
    c._gc = gc  # bypass Credentials entirely
    # reset module caches so fixtures are fresh per test
    sheets._rows_cache.clear()
    sheets._tabs_cache.clear()
    sheets._absen_students_cache.clear()
    sheets._classes_cache.clear()
    sheets._rekap_status_cache.clear()
    sheets._rekap_tab_cache.clear()
    return c, gc


def qual_re(payload):
    return all(re.match(r"^'[^']+'![A-Z]+\d+$", d["range"]) for d in payload["data"])


# ---------- fixture: absen sheets (multi-blok + empty rows) ----------
absen_ilkom = FakeWorksheet("Ilkom", [
    ["JADWAL ABSEN HARI 1"],
    ["Kode Kelas", "CS101"],
    ["NIM", "Nama", "Mode"],
    [],  # blank separator — the old IndexError trigger
    ["26111600001", "Ahmad", "O", "O"],  # col D (idx 3) = pertemuan-1 status
    ["Program Studi", "Ilmu Komputer"],
    ["Kode Kelas", "CS101"],          # second block, same kode, same tab
    ["NIM", "Nama", "Mode"],
    [],  # blank separator
    ["26111600003", "Citra", "S", "S"],
    ["Program Studi", "Sistem Informasi"],
])
absen_manajemen = FakeWorksheet("Manajemen", [
    ["Kode Kelas", "CS101"],
    ["NIM", "Nama", "Mode"],
    [],  # blank separator
    ["26111600002", "Dewi", "A", "A"],
    ["Program Studi", "Manajemen"],
])

c_main, gc = fresh_client()
gc.spreadsheets[SS_ABSEN] = FakeSpreadsheet(SS_ABSEN, [absen_ilkom, absen_manajemen])

# ---------- 3. MULTI-BLOK aggregation ----------
students = c_main._list_students("CS101")
check("multi-blok list aggregates 3 students across 2 tabs",
      len(students) == 3 and {s[0] for s in students} == {"26111600001", "26111600003", "26111600002"},
      f"got {students}")

counts = c_main._absen_counts("CS101", 1)
check("multi-blok counts (total=3 hadir=2 tidak=1)", counts["total"] == 3 and counts["hadir"] == 2
      and counts["feedback"] == 2 and counts["tidak"] == 1, f"got {counts}")

res = c_main._resolve_absen("CS101", ["Ahmad", "Dewi", "26111600003"])
check("resolve across blocks (3 matched, 0 unmatched)",
      len(res["matched"]) == 3 and not res["unmatched"] and not res["ambiguous"],
      f"matched={len(res['matched'])} unmatched={res['unmatched']} ambig={res['ambiguous']}")
titles_matched = {e[4] for e in res["matched"]}
check("resolve hits both prodi tabs", titles_matched == {"Ilkom", "Manajemen"}, f"got {titles_matched}")

# ---------- 2. EMPTY ROWS: read path must not IndexError ----------
res2 = c_main._resolve_absen("CS101", ["gak ada nih"])  # empty rows + short rows present
check("empty/short rows no IndexError in read path",
      res2["unmatched"] == ["gak ada nih"] and len(res2["matched"]) == 0, f"got {res2['unmatched']}")

# ---------- 1+3. WRITE: _update_absen qualified + multi-tab ----------
upd = c_main._update_absen("CS101", 1, ["Ahmad", "Dewi", "26111600003"], "S")
payload = gc.spreadsheets[SS_ABSEN].batch_updates[-1]
check("absen update writes 3 rows", upd["updated"] == 3, f"got {upd}")
check("absen write ranges qualified", qual_re(payload), f"ranges={[d['range'] for d in payload['data']]}")
cellset = {d["range"] for d in payload["data"]}
check("absen write targets right cells (D5,D10,D4)",
      cellset == {"'Ilkom'!D5", "'Ilkom'!D10", "'Manajemen'!D4"}, f"got {cellset}")
check("absen write values are status S", all(d["values"] == [["S"]] for d in payload["data"]))

# ---------- 1. WRITE: _append_record qualified ----------
zoom_ws = FakeWorksheet("Zoom Record", [
    ["Tanggal", "Fasilitator", "Kelas", "Pertemuan"],
    ["15/09/2026", "Fasil A", "CS101", "1", "x", "y", "z", "a", "b", "c", "d", "e", "f", "g", "h"],
    [],  # first truly empty data row -> insert at 3
])
gc.spreadsheets[SS_MAIN] = FakeSpreadsheet(SS_MAIN, [zoom_ws])
rec = sheets.LogRecord("Fasil A", "15/09/2026", "15/09/2026", "1", "CS101", "Algo", "1",
                       "Online", "3", "Reguler", "Dosen X", "13.00", "Zoom 1", "catatan")
rc = c_main._append_record(rec)
pay = gc.spreadsheets[SS_MAIN].batch_updates[-1]
check("append record is ONE batch of 10 unprotected cols",
      len(pay["data"]) == 10, f"got {len(pay['data'])}")
check("append record ranges qualified", qual_re(pay), f"ranges={[d['range'] for d in pay['data']]}")
check("append record hits row 3", all(d["range"].endswith("3") for d in pay["data"]))
check("append record correct columns",
      {d["range"][:6] for d in pay["data"]} == {"'Zoom "} and
      {re.match(r"^'[^']+'!([A-Z]+)", d["range"]).group(1) for d in pay["data"]}
      == {"B", "C", "D", "E", "F", "H", "I", "M", "N", "O"})

# ---------- 1. WRITE: _update_rekap_cells qualified ----------
rekap_ws = FakeWorksheet("Fasil A", [["Tanggal", "Dosen", "Jam"]], row_count=50, col_count=23)
gc.spreadsheets[SS_REKAP] = FakeSpreadsheet(SS_REKAP, [rekap_ws])
c_main._update_rekap_cells("Fasil A", 7, {"O": "3", "Q": "2"})
pay = gc.spreadsheets[SS_REKAP].batch_updates[-1]
check("rekap cell update qualified",
      qual_re(pay) and {d["range"] for d in pay["data"]} == {"'Fasil A'!O7", "'Fasil A'!Q7"},
      f"ranges={[d['range'] for d in pay['data']]}")

# ---------- 4. GRID GUARD raises ----------
small = FakeWorksheet("S", [], row_count=20, col_count=10)
try:
    c_main._guard_grid(small, ["W"], 21)
    check("grid guard raises on past-end row", False, "no raise")
except sheets.SheetsError:
    check("grid guard raises on past-end row", True)
try:
    c_main._guard_grid(small, ["X"], 5)  # X=24 > col_count 10
    check("grid guard raises on past-end col", False, "no raise")
except sheets.SheetsError:
    check("grid guard raises on past-end col", True)
try:
    c_main._guard_grid(small, ["B"], 20)  # within grid (col B=2 <= 10)
    check("grid guard passes in-grid", True)
except sheets.SheetsError as e:
    check("grid guard passes in-grid", False, str(e))

# full-tab append fails clean: row_count=2, row1 has data -> first empty = row 3 > grid
full_zoom = FakeWorksheet("Zoom Record", [["h"], ["15/09/2026", "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "n"]], row_count=2, col_count=26)
gc.spreadsheets[SS_MAIN] = FakeSpreadsheet(SS_MAIN, [full_zoom])
try:
    c_main._append_record(rec)
    check("full-tab append cannot write past grid", False, "no raise")
except sheets.SheetsError:
    check("full-tab append cannot write past grid", True)

# ---------- summary ----------
print(f"\nRESULT PASS={len(PASS)} FAIL={len(FAIL)}")
sys.exit(1 if FAIL else 0)