"""Offline verification stub — feedback sync (SF/OF -> S/O). No live creds.

Checks:
  1. normalisasi NIM: exact full 11-digit + short-NIM suffix (probe: '114' ->
     '...00114'); nama / NIM terpotong / 13-digit TIDAK match.
  2. konversi SF/OF DOANG: sel A/I/kosong/lain tidak disentuh.
  3. short-NIM UNIK guard: suffix '114' ambigu (2 NIM absen berakhiran 114)
     -> tidak dikonversi.
  4. pertemuan ganda '3 dan 4' -> kedua pertemuan diproses (_feedback_sync).
  5. gagal-conversion TIDAK membunuh rekap: _feedback_sync fail-open.
  6. _convert_status menulis qualified ranges + invalidate cache; Q count
     (_feedback_counts refactor) tidak berubah.

Run: py verify_feedback_sync.py
"""
from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
import sheets
from config import Config

SS_MAIN, SS_ABSEN, SS_REKAP = "ss_main", "ss_absen", "ss_rekap"
FEEDBACK_ID = "1dZQcq3TvPh7wkW0z8SF94YExs5jONYf_O3oV09Hk604"
PASS = []
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

    def get_all_values(self):
        return [list(r) for r in self.rows]


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
        semester="1", reminder_slots=((21, 0), (5, 0), (13, 0)), reminder_enabled=False,
        heartbeat_hour=22, heartbeat_minute=0, heartbeat_enabled=False,
    )
    gc = FakeClient({})
    c = sheets.SheetsClient(cfg)
    c._gc = gc
    for cache in (sheets._rows_cache, sheets._tabs_cache, sheets._absen_students_cache,
                  sheets._classes_cache, sheets._rekap_status_cache, sheets._rekap_tab_cache):
        cache.clear()
    sheets._feedback_cache["rows"] = None
    sheets._feedback_cache["ts"] = 0
    return c, gc


def qual_re(payload):
    return all(re.match(r"^'[^']+'![A-Z]+\d+$", d["range"]) for d in payload["data"])


# ---------- fixture absen (data mulai nim_header+2 = idx3; idx2 = filler row) ----------
absen_ilkom = FakeWorksheet("Ilkom", [
    ["Kode Kelas", "3Ilkom"],
    ["NIM", "Nama", "Mode"],
    [],
    ["26110100001", "Ahmad", "S", "SF", "O"],   # p1=SF, p2=O
    ["26110100002", "Budi", "SF", "SF", "O"],   # p1=SF, p2=O
    ["26110100114", "Citra", "O", "O", "OF"],   # p1=O,  p2=OF (unik short '114')
    ["26110100004", "Dewi", "A", "A", "A"],     # p1=A,  p2=A — tak disentuh
    ["Program Studi", "Ilmu Komputer"],
])
absen_manajemen = FakeWorksheet("Manajemen", [
    ["Kode Kelas", "3Ilkom"],
    ["NIM", "Nama", "Mode"],
    [],
    ["26110101114", "Intan", "OF", "OF", "OF"],  # membuat '114' AMBIGU
    ["Program Studi", "Manajemen"],
])

# ---------- fixture feedback (27+ kolom; kolom: NIM=2, School=6, blob=8..24,
# dosen=25, pertemuan=26) ----------
_fb = ["", "", "NIM", "", "", "", "School", "Major", "Subject", "3Ilkom",
       "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "Dosen", "Meetings"]
fb_rows = [
    _fb,
    ["", "", "26110100001", "", "", "", "School of AI & Computer Science",
     "Ilmu Komputer", "Struktur Data", "3Ilkom", "", "", "", "", "", "", "", "", "",
     "", "", "", "", "", "", "Budi Santoso, S.T.,M.T.", "3"],
    ["", "", "26110100002", "", "", "", "School of AI & Computer Science",
     "Ilmu Komputer", "Struktur Data", "3Ilkom", "", "", "", "", "", "", "", "", "",
     "", "", "", "", "", "", "Budi Santoso, S.T.,M.T.", "4"],
    ["", "", "114", "", "", "", "School of AI & Computer Science",
     "Ilmu Komputer", "Struktur Data", "3Ilkom", "", "", "", "", "", "", "", "", "",
     "", "", "", "", "", "", "Budi Santoso, S.T.,M.T.", "3"],
    ["", "", "Abubakar Adib", "", "", "", "School of AI & Computer Science",
     "Ilmu Komputer", "Struktur Data", "3Ilkom", "", "", "", "", "", "", "", "", "",
     "", "", "", "", "", "", "Budi Santoso, S.T.,M.T.", "3"],
    ["", "", "2412010200038", "", "", "", "School of AI & Computer Science",
     "Ilmu Komputer", "Struktur Data", "3Ilkom", "", "", "", "", "", "", "", "", "",
     "", "", "", "", "", "", "Budi Santoso, S.T.,M.T.", "3"],
    ["", "", "26110100", "", "", "", "School of AI & Computer Science",
     "Ilmu Komputer", "Struktur Data", "3Ilkom", "", "", "", "", "", "", "", "", "",
     "", "", "", "", "", "", "Budi Santoso, S.T.,M.T.", "3"],
]

c, gc = fresh_client()
gc.spreadsheets[SS_ABSEN] = FakeSpreadsheet(SS_ABSEN, [absen_ilkom, absen_manajemen])
sheet_abs = c._sheet_in(SS_ABSEN, "Ilkom")


# ---------- 1. NORMALISASI NIM ----------
check("nim exact full 11-digit", c._nim_match("26110100001", "26110100001") is True)
check("nim short suffix unik-nya (probe '114'->'...00114')",
      c._nim_match("26110100114", "114") is True)
check("nim leading-zero style 001->'...00001'", c._nim_match("26110100001", "001") is True)
check("nim beda tidak match", c._nim_match("26110100002", "114") is False)
check("nama-bukan-NIM tidak match", c._nim_match("26110100002", "Abubakar Adib") is False)
check("NIM terpotong tidak match", c._nim_match("261101002", "26110100") is False)
check("13-digit tidak match", c._nim_match("26110100001", "2412010200038") is False)
check("fb lebih panjang dari absen tidak match (guard len)",
      c._nim_match("26110100001", "261101000010") is False)


# ---------- feedback_nims ----------
sheets._feedback_cache["rows"] = fb_rows
sheets._feedback_cache["ts"] = time.time()
nims = c._feedback_nims("3Ilkom", [3, 4], "Budi Santoso, S.T.,M.T.")
check("feedback_nims: NIM valid + short + (nama ikut raw, disaring di convert)",
      {"26110100001", "26110100002", "114", "Abubakar Adib"} <= nims
      and "2412010200038" in nims and "26110100" in nims,
      f"got {sorted(nims)}")
nims3 = c._feedback_nims("3Ilkom", [3], "Budi Santoso, S.T.,M.T.")
check("feedback_nims hanya meeting 3 (exclude NIM meeting 4)",
      "26110100002" not in nims3, f"got {sorted(nims3)}")

# ---------- 2. KONVERSI SF/OF DOANG ----------
p1 = c._plan_convert_status("3Ilkom", 1, {"26110100001", "26110100002"})
check("p1: hanya SF->S (2), A/O tak disentuh",
      len(p1) == 2 and all(x["dari"] == "SF" and x["ke"] == "S" for x in p1)
      and {x["nim"] for x in p1} == {"26110100001", "26110100002"}, f"got {p1}")
p2 = c._plan_convert_status("3Ilkom", 2, {"26110100001", "26110100002"})
check("p2: O/O tak disentuh (0 perubahan)",
      p2 == [], f"got {p2}")

# ---------- 3. SHORT-NIM UNIK GUARD ----------
# Dengan tab Manajemen (26110101114) -> '114' ambiguous -> tak dikonversi.
p2b = c._plan_convert_status("3Ilkom", 2, {"114"})
check("suffix '114' ambigu (2 NIM) -> tidak dikonversi", p2b == [], f"got {p2b}")
# Tanpa tab Manajemen -> '114' unik -> 26110100114 p2 OF->O.
gc.spreadsheets[SS_ABSEN] = FakeSpreadsheet(SS_ABSEN, [absen_ilkom])
for k in [k for k in sheets._rows_cache if k[0] == SS_ABSEN]:
    sheets._rows_cache.pop(k, None)
sheets._tabs_cache.pop(SS_ABSEN, None)
p2c = c._plan_convert_status("3Ilkom", 2, {"114"})
check("suffix '114' unik -> 26110100114 OF->O",
      len(p2c) == 1 and p2c[0]["nim"] == "26110100114" and p2c[0]["dari"] == "OF" and p2c[0]["ke"] == "O",
      f"got {p2c}")

# ---------- 4+5. _convert_status WRITE + INVALIDATE ----------
gc.spreadsheets[SS_ABSEN] = FakeSpreadsheet(SS_ABSEN, [absen_ilkom])
for k in [k for k in sheets._rows_cache if k[0] == SS_ABSEN]:
    sheets._rows_cache.pop(k, None)
sheets._tabs_cache.pop(SS_ABSEN, None)
chg = c._convert_status("3Ilkom", 1, {"26110100001", "26110100002"})
payload = gc.spreadsheets[SS_ABSEN].batch_updates[-1]
check("convert menulis 2 perubahan", len(chg) == 2, f"got {chg}")
check("convert ranges qualified", qual_re(payload), f"got {[d['range'] for d in payload['data']]}")
check("convert menulis kolom D row 4&5 (p1) value S",
      {d["range"] for d in payload["data"]} == {"'Ilkom'!D4", "'Ilkom'!D5"}
      and all(d["values"] == [["S"]] for d in payload["data"]),
      f"got {[d['range'] for d in payload['data']]}")
check("convert invalidate roster (kode)",
      sheets._absen_students_cache.get("3ilkom") is None)
check("convert tidak menyentuh A/I (p2 = 0 perubahan utk set S/O)",
      c._plan_convert_status("3Ilkom", 2, {"26110100001", "26110100002"}) == [])


# ---------- 5. GAGAL-CONVERSION TIDAK BUNUH REKAP (_feedback_sync fail-open) ----------
from handlers.rekap import _feedback_sync  # noqa: E402


class FakeSheetsSync:
    def __init__(self, fail=None):
        self.fail = fail
        self.pms = []

    async def feedback_nims(self, kode, nums, dosen):
        if self.fail == "nims":
            raise sheets.SheetsError("nims boom")
        return {"N1"}

    async def convert_status(self, kode, pm, nims):
        if self.fail == "conv":
            raise sheets.SheetsError("conv boom")
        self.pms.append(pm)
        return [{"dari": "SF"}, {"dari": "OF"}] if pm == 3 else [{"dari": "OF"}]


def ctx_for(fake):
    return SimpleNamespace(bot_data={"sheets": fake})


n_sf, n_of, err = asyncio.run(_feedback_sync(ctx_for(FakeSheetsSync()), "X", "3 dan 4", "Dosen"))
check("pertemuan ganda '3 dan 4': keduanya diproses (3 lalu 4)",
      err is None and n_sf == 1 and n_of == 2, f"got {n_sf},{n_of},{err}")

n_sf, n_of, err = asyncio.run(_feedback_sync(ctx_for(FakeSheetsSync(fail="conv")), "X", "3 dan 4", "Dosen"))
check("gagal konversi -> fail-open (0,0,err) TIDAK raise",
      err is not None and n_sf == 0 and n_of == 0, f"got {n_sf},{n_of},{err}")

n_sf, n_of, err = asyncio.run(_feedback_sync(ctx_for(FakeSheetsSync(fail="nims")), "X", "3 dan 4", "Dosen"))
check("gagal baca feedback -> fail-open", err is not None, f"got {err}")

# ---------- 6. _feedback_counts refactor tidak regresi ----------
sheets._feedback_cache["rows"] = fb_rows
sheets._feedback_cache["ts"] = time.time()
qc = c._feedback_counts("3Ilkom", "3", "Budi Santoso, S.T.,M.T.", "")
check("Q count sesuai filter lama (kode+meeting3+dosen; nama ikut NIM col)",
      qc["q"] == 5, f"got {qc}")
qc4 = c._feedback_counts("3Ilkom", "3 dan 4", "Budi Santoso, S.T.,M.T.", "")
check("Q count pertemuan ganda '3 dan 4'",
      qc4["q"] == 6, f"got {qc4}")


print(f"\nRESULT PASS={len(PASS)} FAIL={len(FAIL)}")
sys.exit(1 if FAIL else 0)