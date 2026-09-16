"""Offline verification stub — Review S3. No creds, gspread stubbed.

Checks:
  1. _append_backup_record / _append_cancel_record call _guard_grid (past-grid -> SheetsError)
  2. callback idx out of range -> clean END / re-ask, never IndexError
     (log.pick_class, rekap.pick_class/pick_sesi/pick_peran, cancel.pick_class,
      backup.pick_class, absen.toggle_check)
Run: py verify_s3.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import sheets
from config import Config
from handlers import absen as h_absen
from handlers import backup as h_backup
from handlers import cancel as h_cancel
from handlers import log as h_log
from handlers import rekap as h_rekap

SS_MAIN = "ss_main"
PASS, FAIL = [], []


def check(label, cond, msg=""):
    (PASS if cond else FAIL).append((label, msg))
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))


# ---------- gspread stubs ----------

class FakeWorksheet:
    def __init__(self, title, rows, row_count=1000, col_count=26):
        self.title, self.rows = title, rows
        self.row_count, self.col_count = row_count, col_count
        self.updates = []

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def update(self, rng, values, value_input_option="USER_ENTERED"):
        self.updates.append((rng, values, value_input_option))


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
        semester="1", reminder_hour=11, reminder_minute=0, reminder_enabled=False,
        heartbeat_hour=22, heartbeat_minute=0, heartbeat_enabled=False,
    )
    c = sheets.SheetsClient(cfg)
    c._gc = FakeClient({SS_MAIN: ss_main})
    sheets._rows_cache.clear()
    sheets._tabs_cache.clear()
    return c


# ---------- 1. backup / cancel append grid guard ----------
bk_rec = sheets.BackupRecord("Fasil A", "Senin, 8 September 2026", "13.00 - 15.30",
                             "CS101", "Algo", "Dosen X", "R1", "Fasil B", "")
cc_rec = sheets.CancelRecord("Dosen X", "Algo", "Senin, 8 September 2026", "13.00 - 15.30",
                             "3", "CS101", "3", "Fasil A")

# backup: row0 info, row1 header, first empty data row = 3
ok_ws = FakeWorksheet("Backup", [["info"], ["header"], []], row_count=10, col_count=10)
c = fresh_client(FakeSpreadsheet(SS_MAIN, [ok_ws]))
c._append_backup_record(bk_rec)
check("backup append writes B{r}:J{r} in-grid",
      ok_ws.updates and ok_ws.updates[-1][0] == "B3:J3", f"got {ok_ws.updates}")

full_ws = FakeWorksheet("Backup", [["info"], ["header"], []], row_count=2, col_count=10)
c = fresh_client(FakeSpreadsheet(SS_MAIN, [full_ws]))
try:
    c._append_backup_record(bk_rec)
    check("backup append past-grid raises SheetsError", False, "no raise")
except sheets.SheetsError:
    check("backup append past-grid raises SheetsError", True)
    check("backup append past-grid wrote nothing", full_ws.updates == [], f"{full_ws.updates}")

narrow_ws = FakeWorksheet("Backup", [["info"], ["header"], []], row_count=10, col_count=5)
c = fresh_client(FakeSpreadsheet(SS_MAIN, [narrow_ws]))
try:
    c._append_backup_record(bk_rec)
    check("backup append too-narrow tab raises SheetsError", False, "no raise")
except sheets.SheetsError:
    check("backup append too-narrow tab raises SheetsError", True)

cc_ws = FakeWorksheet("Cancel", [["header"], []], row_count=10, col_count=9)
c = fresh_client(FakeSpreadsheet(SS_MAIN, [cc_ws]))
c._append_cancel_record(cc_rec)
check("cancel append writes B{r}:I{r} in-grid",
      cc_ws.updates and cc_ws.updates[-1][0] == "B2:I2", f"got {cc_ws.updates}")

cc_full = FakeWorksheet("Cancel", [["header"], []], row_count=1, col_count=9)
c = fresh_client(FakeSpreadsheet(SS_MAIN, [cc_full]))
try:
    c._append_cancel_record(cc_rec)
    check("cancel append past-grid raises SheetsError", False, "no raise")
except sheets.SheetsError:
    check("cancel append past-grid raises SheetsError", True)
    check("cancel append past-grid wrote nothing", cc_full.updates == [], f"{cc_full.updates}")


# ---------- 2. callback idx bounds ----------
class FakeMsg:
    chat_id = 1

    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)

    async def edit_text(self, text, **kw):
        self.sent.append(text)

    async def delete(self):
        pass


class FakeQ:
    def __init__(self, data):
        self.data, self.message = data, FakeMsg()

    async def answer(self, *a, **k):
        pass


class FakeUpdate:
    def __init__(self, q):
        self.callback_query, self.effective_message = q, q.message


class FakeCtx:
    def __init__(self, user_data=None, bot_data=None):
        self.user_data = user_data or {}
        self.bot_data = bot_data or {}


def run(coro):
    return asyncio.run(coro)


def oob(label, fn, data, state, key=None):
    ud = {key: []} if key else {}
    q = FakeQ(data)
    try:
        rc = run(fn(FakeUpdate(q), FakeCtx(ud)))
    except Exception as exc:  # IndexError == fail
        check(label, False, f"raised {type(exc).__name__}: {exc}")
        return
    check(label, rc == state and bool(q.message.sent), f"rc={rc} sent={q.message.sent}")


END = h_log.ConversationHandler.END
oob("log.pick_class OOB idx -> END", h_log.pick_class, "c:5", END, "classes")
oob("rekap.pick_class OOB idx -> END", h_rekap.pick_class, "rkc:5", END, "classes")
oob("cancel.pick_class OOB idx -> END", h_cancel.pick_class, "cc:5", END, "cc_classes")
oob("backup.pick_class OOB idx -> END", h_backup.pick_class, "bk:5", END, "bk_classes")
oob("rekap.pick_sesi OOB idx -> re-ask SESI", h_rekap.pick_sesi, "rks:99", h_rekap.SESI)
oob("rekap.pick_peran OOB idx -> re-ask PERAN", h_rekap.pick_peran, "rkp:99", h_rekap.PERAN)
oob("absen.toggle_check OOB idx -> END", h_absen.toggle_check, "chk:99", END, "absen_students")

# stale-but-present list: idx 2 on a 1-item list must not IndexError either
q = FakeQ("rkc:2")
rc = run(h_rekap.pick_class(FakeUpdate(q), FakeCtx({"classes": [object()]})))
check("rekap.pick_class stale short list -> END", rc == END and bool(q.message.sent), f"rc={rc}")

print(f"\nRESULT PASS={len(PASS)} FAIL={len(FAIL)}")
sys.exit(1 if FAIL else 0)
