"""Offline stub — status "Sakit" end-to-end (kb → pick_status → verbatim write → counts bucket). gspread stubbed.

Run:  py verify_absen_sakit.py
Covers:
  1. _status_kb() punya tombol "🤒 Sakit" callback abs:sakit (lowercase)
  2. pick_status callback abs:sakit -> user_data["absen_status"] == "sakit"
  3. _update_absen(..., "sakit") menulis value "sakit" verbatim (tanpa tanda petik)
  4. _absen_counts: sakit masuk bucket izin (total ya, hadir/feedback tidak), dict punya field "sakit"
  5. regresi: data lama (S/A/I/SF/OF) — hadir/feedback/tidak/belum/izin IDENTIK
     sebelum dan sesudah ada mahasiswa sakit; data lama cuma I tetap aman
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
import sheets
from config import Config

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
    for cache in (sheets._rows_cache, sheets._tabs_cache, sheets._absen_students_cache,
                  sheets._classes_cache):
        cache.clear()
    return c, gc


# Real block geometry (verified live): "Kode Kelas" -> nim_header "NIM" ->
# numbers row nh+1 -> filler-name row nh+2 -> blank -> students.
def _numbers_row():
    return [""] * 3 + [str(p) for p in range(1, 17)]


def _name_row():
    return [""] * 19


# ---------- 1+2. KB + pick_status lowercase ----------
import handlers.absen as h

def _kb_texts(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


def _kb_datas(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


kb = h._status_kb()
check("kb punya tombol '🤒 Sakit'", "🤒 Sakit" in _kb_texts(kb), f"got {_kb_texts(kb)}")
check("kb callback sakit lowercase 'abs:sakit'", "abs:sakit" in _kb_datas(kb), f"got {_kb_datas(kb)}")


def _run_pick_status(data, user_data):
    async def answer():
        return None

    class Msg:
        def __init__(self):
            self.html = []
        async def reply_text(self, txt, **kw):
            self.html.append(txt)

    from types import SimpleNamespace
    msg = Msg()
    q = SimpleNamespace(data=data, message=msg)
    q.answer = answer
    upd = SimpleNamespace(callback_query=q)
    ctx = SimpleNamespace(user_data=user_data)
    state = asyncio.run(h.pick_status(upd, ctx))
    return ctx, msg, state


baseline = {"absen_kode": "CS101", "absen_pertemuan": 3, "absen_identifiers": ["Ahmad"]}
ctx, msg, state = _run_pick_status("abs:sakit", dict(baseline))
check("pick_status abs:sakit -> status 'sakit'",
      ctx.user_data.get("absen_status") == "sakit", f"got {ctx.user_data.get('absen_status')!r}")
check("konfirmasi tampil 'Status: sakit'", any("Status: sakit" in t for t in msg.html), f"got {msg.html}")
check("pick_status kembali ke state CONFIRM", state == h.CONFIRM, f"got {state}")

# status lama tetap valid (regresi kb biasa)
ctx2, _, _ = _run_pick_status("abs:I", dict(baseline))
check("kb lama 'abs:I' masih -> 'I'", ctx2.user_data.get("absen_status") == "I", f"got {ctx2.user_data.get('absen_status')!r}")


# ---------- 3+4+5. counts + verbatim write ----------
def _ilkom(sakit_row):
    return FakeWorksheet("Ilkom", [
        ["JADWAL ABSEN HARI 1"],
        ["Kode Kelas", "CS101"],
        ["NIM", "Nama", "Mode"],
        _numbers_row(),
        _name_row(),
        [],
        ["26111600001", "Ahmad", "O", "S"],   # hadir+feedback
        ["26111600002", "Budi", "O", "SF"],   # hadir + belum
        ["26111600003", "Citra", "O", "A"],   # tidak
        ["26111600004", "Dewi", "O", "I"],    # izin (data lama)
        ["26111600005", "Eka", "O", "OF"],    # hadir + belum
    ] + ([["26111600006", "Farah", "O", sakit_row]] if sakit_row else []) + [
        ["Program Studi", "Ilmu Komputer"],
    ])


def _counts_with(sakit_row):
    c_, gc_ = fresh_client()
    gc_.spreadsheets[SS_ABSEN] = FakeSpreadsheet(SS_ABSEN, [_ilkom(sakit_row)])
    return c_._absen_counts("CS101", 1)


old = _counts_with(None)
check("data lama: total=5 hadir=3 feedback=1 tidak=1 belum=2 izin=1",
      old["total"] == 5 and old["hadir"] == 3 and old["feedback"] == 1
      and old["tidak"] == 1 and old["belum"] == 2 and old["izin"] == 1 and old["sakit"] == 0,
      f"got {old}")

new = _counts_with("sakit")
check("sakit: total+1 izin+1 field sakit=1",
      new["total"] == 6 and new["izin"] == 2 and new["sakit"] == 1, f"got {new}")
check("regresi: hadir/feedback/tidak/belum identik dgn data lama",
      (new["hadir"], new["feedback"], new["tidak"], new["belum"]) ==
      (old["hadir"], old["feedback"], old["tidak"], old["belum"]),
      f"old={old} new={new}")
check("data lama cuma I: kalau gak ada sakit, sakit==0 & izin utuh",
      old["izin"] == 1 and old["sakit"] == 0, f"got old izin={old['izin']} sakit={old['sakit']}")

# ---------- verbatim write 'sakit' ----------
c_w, gc_w = fresh_client()
gc_w.spreadsheets[SS_ABSEN] = FakeSpreadsheet(SS_ABSEN, [_ilkom(None)])
upd = c_w._update_absen("CS101", 1, ["Ahmad"], "sakit")
payload = gc_w.spreadsheets[SS_ABSEN].batch_updates[-1]
written = [(d["range"], d["values"]) for d in payload["data"]]
check("update_absen sakit: range Ilkom!D7 bernilai [['sakit']] verbatim",
      any(rng == "'Ilkom'!D7" and vals == [["sakit"]] for rng, vals in written),
      f"got {written}")

print(f"\n{len(FAIL)} FAIL" if FAIL else "\nALL PASS")
sys.exit(1 if FAIL else 0)