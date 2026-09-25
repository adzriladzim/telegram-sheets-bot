"""Offline verification stub — backup master-join fix (sheets._fetch_backup_classes).

Checks (no creds, gspread stubbed):
  1. Kode dobel beda RomBel -> master row whose RomBel (col 8) matches backup Col H is chosen.
  2. Kode dobel, no RomBel match -> falls back to first kode match.
  3. Zoom only in master Keterangan (col 12) -> keterangan captured -> zoom_label "Zoom 40".
  4. Backup Catatan (Col J) wins over master Keterangan.
Run: py verify_backup_master.py
"""
from __future__ import annotations

from pathlib import Path

import sheets
from config import Config

SS_MAIN = "ss_main"
PASS, FAIL = [], []


def check(label, cond, msg=""):
    (PASS if cond else FAIL).append((label, msg))
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))


class FakeWorksheet:
    def __init__(self, title, rows, row_count=1000, col_count=26):
        self.title, self.rows = title, rows
        self.row_count, self.col_count = row_count, col_count

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


def fresh_client(ss_main):
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


# ---------- fixtures ----------
# Master cols: [4]Kode [5]MK [6]Dosen [8]RomBel [9]SKS [10]ZoomNo [12]Keterangan
def master_row(kode, mk, dosen, rombel, sks, zoom_no, ket=""):
    r = [""] * 16
    r[0], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11], r[12] = (
        "Senin", kode, mk, dosen, rombel, rombel, sks, zoom_no, "https://meet/x", ket)
    return r


# Backup cols: [4]Kode [5]MK [6]Dosen [7]Ruang/RomBel [8]Pengganti [9]Catatan
def _mon_text() -> str:
    """'Senin, <tanggal minggu berjalan>' — fixture harus lolos week-window
    filter _fetch_backup_classes (backup basi tidak dikembalikan)."""
    mon, _ = sheets.week_span_wib()
    return f"Senin, {mon.day} {sheets.ID_MONTHS_INV[f'{mon.month:02d}']} {mon.year}"


def backup_row(kode, room, pengganti, catatan=""):
    r = [""] * 10
    r[0], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9] = (
        "Fasil A", _mon_text(), "13.00 - 15.30", kode, "Algo", "Dosen X",
        room, pengganti, catatan)
    return r


MASTER = [
    ["Header"] * 16,
    master_row("CS101", "Algo", "Dosen X", "3 Ilkom Pro", "3", "28"),          # rombel A
    master_row("CS101", "Algo", "Dosen X", "5 Ilkom Pro", "3", "40"),          # rombel B
    master_row("CS202", "Basis Data", "Dosen Y", "4 Ilkom Pro", "3", "28", "Zoom 40"),  # zoom only in Keterangan
]
BACKUP = [
    ["info"],
    ["header"] * 10,
    backup_row("CS101", "5 Ilkom Pro", "Fasil B"),                            # -> must pick rombel B (zoom 40)
    backup_row("CS202", "4 Ilkom Pro", "Fasil B"),                            # -> zoom from Keterangan
    backup_row("CS101", "9 Ilkom Pro", "Fasil B"),                            # no rombel match -> first match (28)
    backup_row("CS202", "4 Ilkom Pro", "Fasil B", "Zoom 77"),                 # Catatan wins
]

ss = FakeSpreadsheet(SS_MAIN, [FakeWorksheet("Master", MASTER), FakeWorksheet("Backup", BACKUP)])

# case 1: kode dobel beda rombel -> pilih yang match Col H
c = fresh_client(ss)
out = c._fetch_backup_classes("Fasil B")
by_row = {e.row_index: e for e in out}

e1 = by_row[2]
check("1. kode dobel beda rombel -> pilih RomBel match Col H",
      e1.zoom_label == "Zoom 40" and e1.sks == "3" and e1.semester == "5", f"got {e1.zoom_label!r} sks={e1.sks!r} sem={e1.semester!r}")

# case 2: no rombel match -> fallback first kode match (zoom 28)
e3 = by_row[4]
check("2. no RomBel match -> fallback first kode match",
      e3.zoom_label == "Zoom 28", f"got {e3.zoom_label!r}")

# case 3: zoom only in master Keterangan -> terbaca
e2 = by_row[3]
check("3. Zoom hanya di Keterangan -> zoom_label terbaca",
      e2.zoom_label == "Zoom 40" and e2.keterangan == "Zoom 40", f"got {e2.zoom_label!r} ket={e2.keterangan!r}")

# case 4: backup Catatan wins over master Keterangan
e4 = by_row[5]
check("4. Catatan backup menang atas Keterangan master",
      e4.keterangan == "Zoom 77" and e4.zoom_label == "Zoom 77", f"got ket={e4.keterangan!r}")

# case 5: zoom_label fallback ke Nomor Zoom utk entry backup (keterangan kosong)
check("5. zoom_label fallback ke Nomor Zoom saat keterangan kosong",
      sheets.ClassEntry(code="X", subject="", day="", time_range="", category="Backup",
                        lecturer="", room="", rombel="", sks="", zoom_number="28",
                        zoom_link="", keterangan="").zoom_label == "Zoom 28")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for lbl, msg in FAIL:
    print(f"  FAILED: {lbl} {msg}")
raise SystemExit(1 if FAIL else 0)
