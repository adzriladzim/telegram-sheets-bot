"""Offline self-check — absen sesi gabungan (P3+P4 sekaligus), single tetap satu kolom, batal aman.

Run:  py verify_absen_gabungan.py
Covers:
  1. _update_absen(kode, [3, 4], ...) menulis status SAMA ke dua kolom
     (P=pertemuan 3, Q=pertemuan 4, row siswa) + header pengisi di kedua kolom
     per blok — semua blok (multi-prodi).
  2. single int tetap satu kolom (pertemuan 4 -> col G), tanpa header tanpa pengisi.
  3. confirm_cb tombol Batal (:no) -> END + "Dibatalkan.", TANPA write ke sheet.
  4. enter_pertemuan parse: single '4' -> int 4; '3 dan 4'/'3-4'/'3 & 4' -> [3,4];
     invalid ('0', '17', '3 dan 3') -> tetap PERTEMUAN tanpa lanjut.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import sheets
import users
import usage
from config import Config

from handlers.absen import (PERTEMUAN, METHOD, confirm_cb, ConversationHandler,
                            enter_pertemuan, _fmt_pertemuan)

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


name_row = [""] * 19
absen_ilkom = FakeWorksheet("Ilkom", [
    ["JADWAL ABSEN HARI 1"],
    ["Kode Kelas", "CS101"],
    ["NIM", "Nama", "Mode"],
    [""] * 3 + [str(p) for p in range(1, 17)],  # numbers row nh+1
    list(name_row),                               # filler-name row nh+2
    [],
    ["26111600001", "Ahmad", "O", "O"],
    ["Program Studi", "Ilmu Komputer"],
    ["Kode Kelas", "CS101"],
    ["NIM", "Nama", "Mode"],
    [""] * 3 + [str(p) for p in range(1, 17)],
    list(name_row),
    [],
    ["26111600003", "Citra", "S", "S"],
    ["Program Studi", "Sistem Informasi"],
])
absen_manajemen = FakeWorksheet("Manajemen", [
    ["Kode Kelas", "CS101"],
    ["NIM", "Nama", "Mode"],
    [""] * 3 + [str(p) for p in range(1, 17)],
    list(name_row),
    [],
    ["26111600002", "Dewi", "A", "A"],
    ["Program Studi", "Manajemen"],
])

c, gc = fresh_client()
gc.spreadsheets[SS_ABSEN] = FakeSpreadsheet(SS_ABSEN, [absen_ilkom, absen_manajemen])
# 0-based "NIM" header rows: Ilkom 2 & 8, Manajemen 1.
SS = gc.spreadsheets[SS_ABSEN]

# ---------- 1. GABUNGAN [3, 4]: dua kolom sekaligus ----------
upd = c._update_absen("CS101", [3, 4], ["Ahmad", "Dewi", "26111600003"], "S",
                      pengisi="Raihan Syahputra")
payload = SS.batch_updates[-1]
# 3 siswa x 2 kolom (F=pertemuan 3, G=pertemuan 4) + 3 blok x 2 kolom header = 12 cell
check("gabungan writes 12 cells (6 status + 6 header)",
      len(payload["data"]) == 12, f"got {len(payload['data'])}")
status_ranges = {d["range"] for d in payload["data"] if d["values"] == [["S"]]}
check("gabungan status hits F+G per siswa (F7/G7, F14/G14, F6/G6)",
      status_ranges == {"'Ilkom'!F7", "'Ilkom'!G7", "'Ilkom'!F14", "'Ilkom'!G14",
                        "'Manajemen'!F6", "'Manajemen'!G6"},
      f"got {sorted(status_ranges)}")
header_ranges = {d["range"] for d in payload["data"] if d["values"] == [["Raihan Syahputra"]]}
check("gabungan header pengisi pada kedua kolom tiap blok",
      header_ranges == {"'Ilkom'!F5", "'Ilkom'!G5", "'Ilkom'!F12", "'Ilkom'!G12",
                        "'Manajemen'!F4", "'Manajemen'!G4"},
      f"got {sorted(header_ranges)}")
check("gabungan updated count utuh", upd["updated"] == 3, f"got {upd['updated']}")
check("gabungan tak sentuh kolom lain (D..E, H..S)",
      not any(d["range"] in ("'Ilkom'!E7", "'Ilkom'!H7", "'Ilkom'!R7") for d in payload["data"]))

# ---------- 2. SINGLE int: satu kolom saja ----------
SS.batch_updates.clear()
upd1 = c._update_absen("CS101", 4, ["Ahmad"], "S")  # tanpa pengisi
p1 = SS.batch_updates[-1]
check("single int writes SATU kolom (G, 1 cell)",
      len(p1["data"]) == 1 and p1["data"][0] == {"range": "'Ilkom'!G7", "values": [["S"]]},
      f"got {[d['range'] for d in p1['data']]}")
# dengan pengisi, header tetap SATU kolom
SS.batch_updates.clear()
c._update_absen("CS101", 4, ["Ahmad"], "S", pengisi="Raihan Syahputra")
p1h = SS.batch_updates[-1]
check("single header tetap satu kolom, stamp semua blok tab (G5+G12)",
      {d["range"] for d in p1h["data"]} == {"'Ilkom'!G7", "'Ilkom'!G5", "'Ilkom'!G12"},
      f"got {[d['range'] for d in p1h['data']]}")

# ---------- 3. BATAL aman: :no -> END tanpa write ----------
class FakeMsg:
    def __init__(self):
        self.sent = []

    async def delete(self):
        pass

    async def reply_text(self, text, **kw):
        self.sent.append(text)
        return FakeMsg()


class FakeQ:
    def __init__(self, data):
        self.data = data
        self.answered = False
        self.edited = None
        self.message = FakeMsg()

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


SS.batch_updates.clear()
q_no = FakeQ("abc:no")
ctx = FakeContext(c, {"absen_kode": "CS101", "absen_pertemuan": [3, 4],
                      "absen_identifiers": ["Ahmad"], "absen_status": "S"})
rc = asyncio.run(confirm_cb(FakeUpdate(q_no), ctx))
check("batal (abc:no) returns END", rc == ConversationHandler.END, f"got {rc}")
check("batal reply Dibatalkan.",
      any("Dibatalkan." in t for t in q_no.message.sent), f"got {q_no.message.sent}")
check("batal TANPA write ke sheet", len(SS.batch_updates) == 0,
      f"got {len(SS.batch_updates)} batch writes")
check("batal drops buttons", q_no.edited is None, f"got {q_no.edited}")

# ---------- 4. parse enter_pertemuan ----------
async def sim_enter(text):
    sent = []

    async def fake_reply(t, **k):
        sent.append(t)
        return SimpleNamespace()

    upd = SimpleNamespace(
        message=SimpleNamespace(text=text),
        effective_message=SimpleNamespace(reply_text=fake_reply),
    )
    ctx = SimpleNamespace(user_data={})
    rc = await enter_pertemuan(upd, ctx)
    return rc, ctx.user_data.get("absen_pertemuan"), " ".join(sent)


cases = [
    ("4",            METHOD, 4,          ""),
    ("3 dan 4",      METHOD, [3, 4],     ""),
    ("3-4",          METHOD, [3, 4],     ""),
    ("3 - 4",        METHOD, [3, 4],     ""),
    ("3 & 4",        METHOD, [3, 4],     ""),
    ("3Dan4",        METHOD, [3, 4],     ""),
    ("0",            PERTEMUAN, None,    "Masukkan 1-16"),
    ("17",           PERTEMUAN, None,    "Masukkan 1-16"),
    ("3 dan 3",      PERTEMUAN, None,    "dua nomor berbeda"),
    ("abc",          PERTEMUAN, None,    "Masukkan 1-16"),
]
for text, want_rc, want_per, want_err in cases:
    rc, per, replied = asyncio.run(sim_enter(text))
    ok_rc = rc == want_rc
    ok_per = (per == want_per) and (per is not None or want_per is None)
    ok_err = want_err in replied
    check(f"parse '{text}' -> rc={want_rc}, per={want_per}",
          ok_rc and ok_per and (ok_err or not want_err),
          f"rc={rc} per={per} replied={replied!r}")

check("_fmt_pertemuan single", _fmt_pertemuan(4) == "4", _fmt_pertemuan(4))
check("_fmt_pertemuan gabungan", _fmt_pertemuan([3, 4]) == "3 dan 4", _fmt_pertemuan([3, 4]))

print(f"\nRESULT PASS={len([1])} FAIL={len(FAIL)}")
for f in FAIL:
    print("FAILED:", f)
sys.exit(1 if FAIL else 0)