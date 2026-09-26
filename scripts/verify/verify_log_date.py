"""Offline verification stub — /zoom tanggal kelas personal (pilih last/next).

No creds, no sheet access, no telegram server. gspread/functions stubbed.

Checks:
  1. pick_class personal, last != nxt -> state DATE, renders 2 date buttons + back
  2. pick_class personal, last == nxt -> skips date step, goes MEETING (default next)
  3. pick_class backup/make-up -> langsung MEETING (tanggal eksplisit)
  4. pick_date d:last -> lecture_date = last, returns MEETING
  5. pick_date d:next -> lecture_date = next, returns MEETING
  6. pick_date stale (no cls) -> END
  7. pick_class reset override: lecture_date di-pop tiap pick kelas
  8. _class_date: personal tanpa override -> next; dgn override -> override
  9. _class_date: backup -> tanggal eksplisit parse
 10. _build_record: lecture_date = override (personal)
 11. dupe-gate key memakai override (find_conflicts dipanggil dgn tanggal override)
Run: py verify_log_date.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
import sheets
from config import Config
from handlers import log as h_log

PASS, FAIL = [], []


def check(label, cond, msg=""):
    (PASS if cond else FAIL).append((label, msg))
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))


# ---------- deterministic date fakes (Selasa: last=22/09/2026, next=29/09/2026) ----------
_FAKE_LAST, _FAKE_NEXT = "22/09/2026", "29/09/2026"

def _fake_last(day_name):
    return _FAKE_LAST

def _fake_next(day_name):
    return _FAKE_NEXT

sheets.last_date_for_day = _fake_last
sheets.next_date_for_day = _fake_next


def personal_cls(day="Selasa", code="CS101"):
    return sheets.ClassEntry(
        code=code, subject="Algo", day=day, time_range="19.00 - 21.00",
        category="Reguler", lecturer="Dosen X", room="R1", rombel="", sks="3",
        zoom_number="33", zoom_link="", backup_hari_tanggal="", semester="1")


def backup_cls(date_str="Selasa, 22 September 2026"):
    c = personal_cls()
    c.category = "Backup"
    c.backup_hari_tanggal = date_str
    return c


# ---------- telegram fakes ----------
class FakeMsg:
    chat_id = 1

    def __init__(self):
        self.sent = []
        self.buttons = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)
        rmk = kw.get("reply_markup")
        if rmk is not None:
            for row in rmk.inline_keyboard:
                for b in row:
                    self.sent.append(b.text)
                    self.buttons.append(b.callback_data)
        return self

    async def edit_text(self, text, **kw):
        self.sent.append(text)

    async def delete(self):
        pass

    def _to_dict(self):  # pragma: no cover
        return {}


class FakeQ:
    def __init__(self, data):
        self.data, self.message = data, FakeMsg()

    async def answer(self, *a, **k):
        pass


class FakeUpdate:
    def __init__(self, q):
        self.callback_query, self.effective_message = q, q.message


class FakeSheets:
    def __init__(self, conflict_report=None):
        self.conflicts = conflict_report or []
        self.found_calls = []

    async def get_next_meeting(self, kode):
        return ("2", "3", "3 dan 4")

    async def find_conflicts(self, code, tanggal, pertemuan):
        self.found_calls.append((code, tanggal, pertemuan))
        return self.conflicts


class FakeCtx:
    def __init__(self, user_data=None):
        self.user_data = user_data or {}
        self.bot_data = {"cfg": Config(
            bot_token="x", sheet_id="ss_main", service_account_json=Path("secrets/none.json"),
            facilitator_name="", master_sheet="Master", zoom_record_sheet="Zoom Record",
            backup_sheet="Backup", cancel_sheet="Cancel", absen_sheet_id="ss_absen",
            absen_sheet_name="Absen", rekap_sheet_id="ss_rekap", rekap_bukti_folder_id="",
            semester="1", reminder_slots=((11, 0),), reminder_enabled=False,
            heartbeat_hour=22, heartbeat_minute=0, heartbeat_enabled=False,
        )}


def run(coro):
    return asyncio.run(coro)


def patch_sheets(ud, fake):
    ctx = FakeCtx(ud)
    h_log._sheets = lambda context: fake  # noqa: E731
    return ctx


END = h_log.ConversationHandler.END

# 1. personal last != nxt -> DATE, date buttons rendered
fs = FakeSheets()
q = FakeQ("c:0")
ctx = FakeCtx({"classes": [personal_cls()], "facilitator": "Fasil A"})
h_log._sheets = lambda context: fs  # noqa: E731
rc = run(h_log.pick_class(FakeUpdate(q), ctx))
joined = "\n".join(q.message.sent)
check("pick_class personal -> DATE", rc == h_log.DATE, f"rc={rc}")
check("pick_class date KB punya last & next",
      "29 September 2026" in joined and "22 September 2026" in joined, joined)
check("pick_class date KB default dgn d:next",
      "d:next" in q.message.buttons and "d:last" in q.message.buttons,
      str(q.message.buttons))

# 2. personal last == nxt -> MEETING
sheets.next_date_for_day = lambda d: _FAKE_LAST  # force equal
q = FakeQ("c:0")
ctx = FakeCtx({"classes": [personal_cls()], "facilitator": "Fasil A"})
rc = run(h_log.pick_class(FakeUpdate(q), ctx))
check("pick_class last==nxt -> MEETING default next", rc == h_log.MEETING, f"rc={rc}")
sheets.next_date_for_day = _fake_next  # restore

# 3. backup class -> langsung MEETING (no date question)
q = FakeQ("c:0")
ctx = FakeCtx({"classes": [backup_cls()], "facilitator": "Fasil A"})
rc = run(h_log.pick_class(FakeUpdate(q), ctx))
joined = "\n".join(q.message.sent)
check("pick_class backup -> MEETING", rc == h_log.MEETING, f"rc={rc}")
check("pick_class backup tanpa pertanyaan tanggal", "Tanggal kelasnya" not in joined, joined)

# 4. pick_date d:last
q = FakeQ("d:last")
ctx = FakeCtx({"cls": personal_cls(), "facilitator": "Fasil A"})
rc = run(h_log.pick_date(FakeUpdate(q), ctx))
check("pick_date d:last -> MEETING", rc == h_log.MEETING, f"rc={rc}")
check("pick_date d:last set lecture_date = last",
      ctx.user_data.get("lecture_date") == _FAKE_LAST, str(ctx.user_data.get("lecture_date")))

# 5. pick_date d:next
q = FakeQ("d:next")
ctx = FakeCtx({"cls": personal_cls(), "facilitator": "Fasil A"})
rc = run(h_log.pick_date(FakeUpdate(q), ctx))
check("pick_date d:next -> MEETING", rc == h_log.MEETING, f"rc={rc}")
check("pick_date d:next set lecture_date = next",
      ctx.user_data.get("lecture_date") == _FAKE_NEXT, str(ctx.user_data.get("lecture_date")))

# 6. pick_date stale
q = FakeQ("d:last")
ctx = FakeCtx({})
rc = run(h_log.pick_date(FakeUpdate(q), ctx))
check("pick_date tanpa cls -> END", rc == END, f"rc={rc}")

# 7. pick_class reset override tiap pick
q = FakeQ("c:0")
ctx = FakeCtx({"classes": [personal_cls()], "facilitator": "Fasil A", "lecture_date": _FAKE_LAST})
rc = run(h_log.pick_class(FakeUpdate(q), ctx))
check("pick_class pop lecture_date lama", "lecture_date" not in ctx.user_data, str(ctx.user_data))

# 8. _class_date personal tanpa/dgn override
c = personal_cls()
check("_class_date default next", h_log._class_date(FakeCtx({}), c) == _FAKE_NEXT)
check("_class_date pakai override", h_log._class_date(FakeCtx({"lecture_date": _FAKE_LAST}), c) == _FAKE_LAST)

# 9. _class_date backup eksplisit
check("_class_date backup eksplisit",
      h_log._class_date(FakeCtx({}), backup_cls()) == "22/09/2026")

# 10. _build_record lecture_date = override
c = personal_cls()
ud = {"cls": c, "facilitator": "Fasil A", "meeting": "3", "scheme": "Online",
      "zoom": "Zoom 33", "notes": "", "lecture_date": _FAKE_LAST}
rec = h_log._build_record(FakeCtx(ud))
check("_build_record pakai override", rec.lecture_date == _FAKE_LAST, rec.lecture_date)

# 11. dupe-gate key memakai override (via pick_date -> _meeting_warnings -> find_conflicts)
q = FakeQ("d:last")
fs = FakeSheets()
ctx = FakeCtx({"cls": personal_cls(), "facilitator": "Fasil A"})
h_log._sheets = lambda context: fs  # noqa: E731
rc = run(h_log.pick_date(FakeUpdate(q), ctx))
check("pick_date -> MEETING (dupe-gate path)", rc == h_log.MEETING, f"rc={rc}")
check("dupe-gate find_conflicts pakai override",
      fs.found_calls and fs.found_calls[-1][1] == _FAKE_LAST, str(fs.found_calls))

print(f"\nRESULT PASS={len(PASS)} FAIL={len(FAIL)}")
sys.exit(1 if FAIL else 0)