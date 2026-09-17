"""Verify multi-slot reminder: config parse + backward compat, unique job names,
done-map filter (smart slot), skip-send when all classes logged.
Run: py verify_reminder_slots.py  (no network needed)
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import sheets
import users
from config import Config, _int_list, _reminder_slots
from handlers import reminder

N = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global N
    N += 1
    assert cond, f"FAIL {name}: {detail}"


def cfg(slots: tuple[tuple[int, int], ...]) -> Config:
    return Config(
        bot_token="x", sheet_id="x", service_account_json=Path("x"), facilitator_name="",
        master_sheet="", zoom_record_sheet="", backup_sheet="", cancel_sheet="",
        absen_sheet_id="", absen_sheet_name="", rekap_sheet_id="", rekap_bukti_folder_id="",
        semester="1", reminder_slots=slots, reminder_enabled=True,
        heartbeat_hour=22, heartbeat_minute=0, heartbeat_enabled=False)


def cls(code: str, day: str, cat: str = "Reguler", backup_date: str = "") -> sheets.ClassEntry:
    return sheets.ClassEntry(
        code=code, subject="Algo", day=day, time_range="08.00 - 10.00", category=cat,
        lecturer="Dosen", room="R1", rombel="A", sks="3", zoom_number="1", zoom_link="",
        backup_hari_tanggal=backup_date)


# ---------- 1. config parse ----------
with patch.dict(os.environ, {"REMINDER_TIMES": "21,5,13"}, clear=False):
    check("env REMINDER_HOUR absent -> times parsed", _reminder_slots() == ((21, 0), (5, 0), (13, 0)),
          str(_reminder_slots()))
with patch.dict(os.environ, {"REMINDER_HOUR": "21", "REMINDER_MINUTE": "30"}, clear=False):
    check("legacy REMINDER_HOUR -> single slot w/ minute", _reminder_slots() == ((21, 30),),
          str(_reminder_slots()))
with patch.dict(os.environ, {"X": "1, 2, 3"}, clear=False):
    check("_int_list normal", _int_list("X", "9") == (1, 2, 3))
with patch.dict(os.environ, {"X": "  "}, clear=False):
    check("_int_list blank -> empty", _int_list("X", "7") == ())

# ---------- 2. unique job name per hour ----------
names = {reminder._job_name(7, h, m) for h, m in ((21, 0), (5, 0), (13, 0))}
check("job names unique per slot", len(names) == 3, str(names))
check("job name includes HHMM", "reminder:7:2100" in names)

# ---------- 3. full-slot detection ----------
c = cfg(((21, 0), (5, 0), (13, 0)))
check("21 UTC (04:00 WIB) = full", reminder._is_full_slot(c, "reminder:7:2100"))
check("5 UTC = smart", not reminder._is_full_slot(c, "reminder:7:0500"))
check("13 UTC = smart", not reminder._is_full_slot(c, "reminder:7:1300"))
check("legacy single slot = full", reminder._is_full_slot(cfg(((21, 30),)), "reminder:7:2130"))


# ---------- 4. done-map filter ----------
class FakeSheets:
    def __init__(self, classes, done):
        self.classes, self.done = classes, done

    async def get_all_loggable_classes(self, facilitator):
        return (self.classes, [], [])

    async def get_done_by_date(self, facilitator):
        return self.done


today = sheets.today_day_wib()
now = sheets.today_str_wib()
a, b = cls("CS101", today), cls("CS202", today)
two = [a, b]


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


done_ab = {(a.code.casefold(), now), (b.code.casefold(), now)}
check("all done -> filtered empty", run(reminder._filter_todo(FakeSheets(two, done_ab), "F", two)) == [], "still sent")
check("done-a only -> b kept", [x.code for x in run(reminder._filter_todo(FakeSheets(two, {(a.code.casefold(), now)}), "F", two))] == ["CS202"])
check("no done -> both kept", len(run(reminder._filter_todo(FakeSheets(two, set()), "F", two))) == 2)
check("done wrong date -> kept (bukan hari ini)",
      len(run(reminder._filter_todo(FakeSheets(two, {(a.code.casefold(), "01/01/2000")}), "F", two))) == 2)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append((chat_id, text, parse_mode))
        return None


class FakeJob:
    def __init__(self, chat_id, name):
        self.chat_id, self.name = chat_id, name


class FakeCtx:
    def __init__(self, job, sheets_client):
        self.job, self.bot = job, FakeBot()
        self.bot_data = {"cfg": c if "c" in globals() else None, "sheets": sheets_client}


async def _run_job(job, fc):
    await reminder.reminder_job(fc)


# ---------- 5. reminder_job end-to-end ----------
_tmp = tempfile.mkdtemp(prefix="verify_reminder_")
_reg_path = Path(_tmp) / "users.json"
_reg_path.write_text(json.dumps({"123": "Fasil Test"}), encoding="utf-8")
users.init(_reg_path, default_name="")
# 5a. smart slot + all done -> no send
ctx = FakeCtx(FakeJob(123, "reminder:123:0500"), FakeSheets(two, done_ab))
run(_run_job(ctx.job, ctx))
check("smart all-done -> skip send", ctx.bot.sent == [], f"sent {len(ctx.bot.sent)}")

# 5b. smart slot + one todo -> sends only remaining + footer
ctx = FakeCtx(FakeJob(123, "reminder:123:0500"), FakeSheets(two, {(a.code.casefold(), now)}))
run(_run_job(ctx.job, ctx))
check("smart one-todo -> 1 message", len(ctx.bot.sent) == 1, str(len(ctx.bot.sent)))
txt = ctx.bot.sent[0][1]
check("smart header", "belum di-log" in txt and "CS202" in txt and "CS101" not in txt)
check("footer absen+rekap", "/absen + /rekap" in txt)
check("HTML parse mode", ctx.bot.sent[0][2] == "HTML")

# 5c. morning slot -> full schedule, no done-map consult, no footer filter
boom = FakeSheets(two, None)  # done-map would raise if consulted


async def _boom_done(facilitator):
    raise AssertionError("done-map consulted on full slot")


boom.get_done_by_date = _boom_done
ctx = FakeCtx(FakeJob(123, "reminder:123:2100"), boom)
run(_run_job(ctx.job, ctx))
check("full slot -> 1 message", len(ctx.bot.sent) == 1, str(len(ctx.bot.sent)))
check("full header has count", "ada 2 kelas" in ctx.bot.sent[0][1], ctx.bot.sent[0][1])
check("full slot no todo footer", "/absen + /rekap" not in ctx.bot.sent[0][1])

print(f"OK — {N} checks passed")