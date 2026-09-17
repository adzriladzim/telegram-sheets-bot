"""Offline stub — rekap separator rows (garis pemisah hitam) + append placement.

Simulates the campus's black separator line as an EMPTY row between period
blocks (plus a heavy-border variant). Verifies _append_rekap_record inserts:

  1. no separator             -> legacy: first empty row after header / append
  2. empty-row separator      -> below the line, NOT on it (the photographed bug)
  3. gap above the line       -> gap NOT reused; insert stays below last line
  4. heavy-border separator   -> detected as separator, insert below it
  5. separator cache          -> invalidated after a write (TTL _ROWS_TTL)

Run: py verify_rekap_separator.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import sheets
from config import Config

SS_REKAP = "ss_rekap"
RECORDS = []


def rec():
    """Fresh (kode, pertemuan) pair per call so keys don't dedupe."""
    RECORDS.append(len(RECORDS) + 1)
    n = RECORDS[-1]
    return sheets.RekapRecord(
        facilitator="F", tanggal=f"{n} Sept 2026", lecturer="Dosen X",
        jam="10.00 - 12.00", kode=f"K{n}", subject="Matkul", sks="3",
        pertemuan=str(n), tipe="On-site", sesi="Kelas Biasa",
        peran="Fasilitator Kelas", bukti="https://drive.google.com/x",
        bukti_name="foto.jpg")


def check(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        sys.exit(1)


def grid_from_rows(rows, heavy=None):
    """Grid JSON as returned by Sheets API (userEnteredFormat only)."""
    heavy = heavy or []
    out = []
    for i in range(len(rows)):
        values = []
        if i in heavy:
            values = [None] * 3  # A,B,C cells; heavy border on B only
            values[1] = {"userEnteredFormat": {"borders": {
                "top": {"style": "SOLID_THICK", "width": 1}}}}
        out.append({"values": values})
    return {"sheets": [{"data": [{"rowData": out}]}]}


class FakeWorksheet:
    def __init__(self, title, rows, row_count=1000, col_count=26):
        self.title = title
        self.rows = rows
        self.row_count = row_count
        self.col_count = col_count
        self.updates = []

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def update(self, range_name, values, value_input_option=""):
        self.updates.append((range_name, values))


class FakeSpreadsheet:
    def __init__(self, ws):
        self._ws = {ws.title: ws}

    def worksheet(self, title):
        return self._ws[title]

    def worksheets(self):
        return list(self._ws.values())


class FakeHttp:
    def __init__(self, grids_by_tab):
        self.grids_by_tab = grids_by_tab

    def spreadsheets_get(self, ss_id, params=None):
        import re
        m = re.match(r"'([^']+)'", (params or {}).get("ranges", ""))
        return self.grids_by_tab[m.group(1)]


class FakeClient:
    def __init__(self, ws, grids):
        self.ws = ws
        self.http_client = FakeHttp(grids)

    def open_by_key(self, ss_id):
        return FakeSpreadsheet(self.ws)


def make_client(rows, heavy=None):
    ws = FakeWorksheet("Adzril Adzim", rows)
    grids = {"Adzril Adzim": grid_from_rows(rows, heavy)}
    cfg = Config(
        bot_token="x", sheet_id="ss", service_account_json=Path("secrets/none.json"),
        facilitator_name="", master_sheet="M", zoom_record_sheet="Z",
        backup_sheet="B", cancel_sheet="C", absen_sheet_id="a",
        absen_sheet_name="A", rekap_sheet_id=SS_REKAP, rekap_bukti_folder_id="",
        semester="1", reminder_slots=((21, 0),), reminder_enabled=False,
        heartbeat_hour=22, heartbeat_minute=0, heartbeat_enabled=False,
    )
    c = sheets.SheetsClient(cfg)
    c._gc = FakeClient(ws, grids)
    sheets._separator_cache.clear()
    sheets._rows_cache.clear()
    sheets._tabs_cache.clear()
    return c, ws


def scenario1_no_separator():
    """Contiguous data, no empty rows -> legacy appends at end."""
    c, ws = make_client([
        ["No.", "Tanggal", "Dosen", "Jam", "Kode", "MK", "P", "SKS"],
        ["1", "9 Sept 2026", "A", "10", "K1", "M", "1", "3"],
        ["2", "10 Sept 2026", "B", "11", "K2", "M", "2", "3"],
        ["3", "11 Sept 2026", "C", "12", "K3", "M", "3", "3"],
    ])
    row = c._append_rekap_record("Adzril Adzim", rec())
    check("no-separator appends at end (row 5)", row == 5, f"got row {row}")
    check("no-separator wrote A5:W5", ws.updates[0][0] == "A5:W5", ws.updates)


def scenario2_empty_row_separator():
    """The photographed bug: empty separator row (line) between blocks.
    Old code picked the line itself (row 4). New code goes below: row 7."""
    c, ws = make_client([
        ["No.", "Tanggal", "Dosen", "Jam", "Kode", "MK", "P", "SKS"],
        ["1", "9 Sept 2026", "A", "10", "K1", "M", "1", "3"],
        ["2", "10 Sept 2026", "B", "11", "K2", "M", "2", "3"],
        [],  # row 4: GARIS PEMISAH (empty separator)
        ["3", "18 Sept 2026", "C", "12", "K3", "M", "3", "3"],
        ["4", "20 Sept 2026", "D", "13", "K4", "M", "4", "3"],
    ])
    seps = c._separator_rows(SS_REKAP, "Adzril Adzim")
    check("separator detected at row 4", seps == [3], f"got {seps}")
    row = c._append_rekap_record("Adzril Adzim", rec())
    check("insert below separator (row 7)", row == 7, f"got row {row}")
    check("did NOT write on the line (A4)", all("A4" not in u[0] for u in ws.updates), ws.updates)


def scenario3_gap_above_line_skipped():
    """Empty row ABOVE the separator must NOT be reused as insert target."""
    c, ws = make_client([
        ["No.", "Tanggal", "Dosen", "Jam", "Kode", "MK", "P", "SKS"],
        ["1", "9 Sept 2026", "A", "10", "K1", "M", "1", "3"],
        [],                            # empty gap ABOVE the separator (row 3)
        ["2", "10 Sept 2026", "B", "11", "K2", "M", "2", "3"],
        [],                            # separator line (row 5)
        ["3", "18 Sept 2026", "C", "12", "K3", "M", "3", "3"],
    ])
    seps = c._separator_rows(SS_REKAP, "Adzril Adzim")
    check("separators = rows 3 and 5", seps == [2, 4], f"got {seps}")
    row = c._append_rekap_record("Adzril Adzim", rec())
    check("insert below last line (row 7)", row == 7, f"got row {row}")
    check("gap above the line NOT reused", all("A3" not in u[0] for u in ws.updates), ws.updates)


def scenario4_heavy_border_separator():
    """Heavy border row (SOLID_THICK) is detected even with contiguous data."""
    c, ws = make_client([
        ["No.", "Tanggal", "Dosen", "Jam", "Kode", "MK", "P", "SKS"],
        ["1", "9 Sept 2026", "A", "10", "K1", "M", "1", "3"],
        ["2", "10 Sept 2026", "B", "11", "K2", "M", "2", "3"],
        ["3", "11 Sept 2026", "C", "12", "K3", "M", "3", "3"],
    ], heavy=[2])
    seps = c._separator_rows(SS_REKAP, "Adzril Adzim")
    check("heavy-border row detected (row 3)", seps == [2], f"got {seps}")
    row = c._append_rekap_record("Adzril Adzim", rec())
    check("insert below heavy line (row 5)", row == 5, f"got row {row}")


def scenario5_cache_invalidated_on_write():
    c, ws = make_client([
        ["No.", "Tanggal", "Dosen", "Jam", "Kode", "MK", "P", "SKS"],
        ["1", "9 Sept 2026", "A", "10", "K1", "M", "1", "3"],
        [],
        ["2", "10 Sept 2026", "B", "11", "K2", "M", "2", "3"],
    ])
    c._separator_rows(SS_REKAP, "Adzril Adzim")
    check("separator cached", (SS_REKAP, "Adzril Adzim") in sheets._separator_cache)
    c._append_rekap_record("Adzril Adzim", rec())
    check("separator cache invalidated after write",
          (SS_REKAP, "Adzril Adzim") not in sheets._separator_cache)


if __name__ == "__main__":
    scenario1_no_separator()
    scenario2_empty_row_separator()
    scenario3_gap_above_line_skipped()
    scenario4_heavy_border_separator()
    scenario5_cache_invalidated_on_write()
    print("\nALL PASS")