"""Offline stub — absen header pengisi write + confirm_cb stale guard. gspread stubbed.

Run:  py verify_absen_header.py
Covers:
  1. _update_absen(pengisi=...) writes the full name into the filler-NAME row
     (A1 row nim_header+3 = 0-based nim_header+2, col pertemuan D..S — directly
     below the meeting NUMBERS row, per live convention verified by
     verify_pengisi_probe.py) for EVERY touched block (multi-blok)
  2. no pengisi -> no header write (backward compat, old stub still passes)
  3. confirm_cb: stale guard returns END with the "sesi selesai" message
  4. confirm_cb success: reply includes "Pengisi:", buttons dropped, usage.log called
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import sheets
import users
import usage
from config import Config

from handlers.absen import confirm_cb, ConversationHandler

SS_MAIN, SS_ABSEN, SS_REKAP = "ss_main", "ss_absen", "ss_rekap"
FAIL = []


def check(label, cond, msg=""):
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))
    if not cond:
        FAIL.append(label)


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
    sheets._rows_cache.clear()
    sheets._tabs_cache.clear()
    sheets._absen_students_cache.clear()
    sheets._classes_cache.clear()
    return c, gc


# Real block geometry (verified live by verify_pengisi_probe.py):
#   row "Kode Kelas" -> legend rows -> row nim_header "NIM" -> nim_header+1
#   numbers row (D=1..S=16) -> nim_header+2 filler-NAME row -> blank -> students.
_NUM = ["D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S"]  # unused labels
numbers_row = ["D"] + [str(p) for p in range(1, 17)]
numbers_row = [""] * 3 + [str(p) for p in range(1, 17)]   # A,B,C empty; D..S = 1..16
name_row = [""] * 19

absen_ilkom = FakeWorksheet("Ilkom", [
    ["JADWAL ABSEN HARI 1"],
    ["Kode Kelas", "CS101"],
    ["NIM", "Nama", "Mode"],
    [""] * 3 + [str(p) for p in range(1, 17)],  # numbers row nh+1
    list(name_row),                               # filler-name row nh+2
    [],                                           # blank
    ["26111600001", "Ahmad", "O", "O"],
    ["Program Studi", "Ilmu Komputer"],
    ["Kode Kelas", "CS101"],
    ["NIM", "Nama", "Mode"],
    [""] * 3 + [str(p) for p in range(1, 17)],  # numbers row nh+1
    list(name_row),                               # filler-name row nh+2
    [],
    ["26111600003", "Citra", "S", "S"],
    ["Program Studi", "Sistem Informasi"],
])
absen_manajemen = FakeWorksheet("Manajemen", [
    ["Kode Kelas", "CS101"],
    ["NIM", "Nama", "Mode"],
    [""] * 3 + [str(p) for p in range(1, 17)],  # numbers row nh+1
    list(name_row),                               # filler-name row nh+2
    [],
    ["26111600002", "Dewi", "A", "A"],
    ["Program Studi", "Manajemen"],
])

c, gc = fresh_client()
gc.spreadsheets[SS_ABSEN] = FakeSpreadsheet(SS_ABSEN, [absen_ilkom, absen_manajemen])
# 0-based "NIM" header rows per block: Ilkom 2 & 8, Manajemen 1.
NIM_HEADERS = {"Ilkom": (2, 8), "Manajemen": (1)}

# ---------- 1. header write, multi-blok ----------
upd = c._update_absen("CS101", 1, ["Ahmad", "Dewi", "26111600003"], "S", pengisi="Raihan Syahputra")
payload = gc.spreadsheets[SS_ABSEN].batch_updates[-1]
check("multi-blok update writes 3 status + 3 header cells",
      len(payload["data"]) == 6, f"got {len(payload['data'])}")
header_ranges = {d["range"] for d in payload["data"] if d["values"] == [["Raihan Syahputra"]]}
check("header (name) written on EVERY touched block at A1 row nim_header+3",
      header_ranges == {"'Ilkom'!D5", "'Ilkom'!D12", "'Manajemen'!D4"}, f"got {header_ranges}")
status_ranges = {d["range"] for d in payload["data"] if d["values"] == [["S"]]}
check("status cells unchanged",
      status_ranges == {"'Ilkom'!D7", "'Ilkom'!D14", "'Manajemen'!D6"}, f"got {status_ranges}")
check("result returns pengisi", upd.get("pengisi") == "Raihan Syahputra", f"got {upd.get('pengisi')}")
check("result lists header cells", set(upd.get("header_cells", [])) == header_ranges,
      f"got {upd.get('header_cells')}")

# header also respects pertemuan column (e.g. pertemuan 4 -> col G)
gc.spreadsheets[SS_ABSEN].batch_updates.clear()
c._update_absen("CS101", 4, ["Ahmad"], "O", pengisi="Raihan Syahputra")
p4 = gc.spreadsheets[SS_ABSEN].batch_updates[-1]
check("pertemuan 4 header lands in col G at A1 row nim_header+3",
      "'Ilkom'!G5" in {d["range"] for d in p4["data"]},
      f"got {[d['range'] for d in p4['data']]}")
# verify the NAME row (0-based nh+2), not the numbers row (0-based nh+1), is targeted:
p4rng = {d["range"] for d in p4["data"]}
check("pertemuan 4 write does NOT touch numbers row (E..S row 4)",
      "'Ilkom'!G4" not in p4rng and "'Ilkom'!D4" not in p4rng,
      f"got {p4rng}")

# ---------- 2. no pengisi -> no header write ----------
gc.spreadsheets[SS_ABSEN].batch_updates.clear()
c._update_absen("CS101", 1, ["Ahmad", "Dewi", "26111600003"], "S")
p0 = gc.spreadsheets[SS_ABSEN].batch_updates[-1]
check("no pengisi -> no header cell in batch",
      len(p0["data"]) == 3 and all(d["values"] == [["S"]] for d in p0["data"]),
      f"got {[d['range'] for d in p0['data']]}")

# ---------- fake telegram bits for confirm_cb ----------
class FakeMsg:
    def __init__(self):
        self.deleted = False
        self.sent = []

    async def delete(self):
        self.deleted = True

    async def reply_text(self, text, **kw):
        self.sent.append(text)
        return FakeMsg()


class FakeQ:
    def __init__(self, data, send=None):
        self.data = data
        self.answered = False
        self.edited = None
        self.message = send or FakeMsg()

    async def answer(self):
        self.answered = True

    async def edit_message_reply_markup(self, **kw):
        self.edited = kw.get("reply_markup", "MISSING")


class FakeUpdate:
    def __init__(self, q):
        self.callback_query = q
        self.effective_chat = type("EC", (), {"id": 12345})()

    def __getattr__(self, name):
        return getattr(self.callback_query, name)


class FakeContext:
    def __init__(self, sheets_client, user_data):
        self.bot_data = {"sheets": sheets_client}
        self.user_data = user_data


# ---------- 3. stale guard ----------
q_s = FakeQ("abc:ok")
ctx_s = FakeContext(c, {})  # no absen keys
rc = asyncio.run(confirm_cb(FakeUpdate(q_s), ctx_s))
check("stale guard returns END", rc == ConversationHandler.END, f"got {rc}")
check("stale guard reply says sesi selesai",
      any("Sesi absen sudah selesai" in t for t in q_s.message.sent), f"got {q_s.message.sent}")
check("stale guard drops buttons", q_s.edited is None, f"got {q_s.edited}")

# ---------- 4. success path ----------
real_get = users.get
users.get = lambda chat_id: "Raihan Syahputra"
usage_logged = []
usage.log = lambda *a, **k: usage_logged.append((a, k))

q_ok = FakeQ("abc:ok")
ctx_ok = FakeContext(c, {"absen_kode": "CS101", "absen_pertemuan": 1,
                         "absen_identifiers": ["Ahmad"], "absen_status": "S"})
rc2 = asyncio.run(confirm_cb(FakeUpdate(q_ok), ctx_ok))
users.get = real_get
check("success returns END", rc2 == ConversationHandler.END, f"got {rc2}")
check("success reply includes Pengisi:",
      any("👤 Pengisi: Raihan Syahputra" in t for t in q_ok.message.sent),
      f"got {q_ok.message.sent}")
check("success drops confirm buttons", q_ok.edited is None, f"got {q_ok.edited}")
check("usage logged with full name", usage_logged and usage_logged[0][0][1] == "Raihan Syahputra",
      f"got {usage_logged}")
header_payload = gc.spreadsheets[SS_ABSEN].batch_updates[-1]
check("success path wrote header cell",
      any(d["values"] == [["Raihan Syahputra"]] for d in header_payload["data"]),
      f"got {[d['range'] for d in header_payload['data']]}")

print(f"\nRESULT PASS={len([1])} FAIL={len(FAIL)}")
for f in FAIL:
    print("FAILED:", f)
sys.exit(1 if FAIL else 0)