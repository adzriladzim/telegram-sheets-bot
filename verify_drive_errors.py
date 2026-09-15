"""Offline stub — HEAD verify Drive-error handling in sheets.py.

Checks:
  1. _run converts googleapiclient HttpError (403/404/429) -> SheetsError with status in message
  2. _bukti_folder_for maps HttpError 403/404 -> SheetsError("Drive: {status} {reason}")
Run: py verify_drive_errors.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import googleapiclient.errors
import sheets
from config import Config

PASS = []
FAIL = []


def check(label, cond, msg=""):
    (PASS if cond else FAIL).append((label, msg))
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))


class FakeResp:
    def __init__(self, status, reason):
        self.status = status
        self.reason = reason


def http_err(status, reason="error"):
    return googleapiclient.errors.HttpError(FakeResp(status, reason), b"")


def fresh_client(folder_parent="FOLDER_PARENT"):
    cfg = Config(
        bot_token="x", sheet_id="ss_main", service_account_json=Path("secrets/none.json"),
        facilitator_name="", master_sheet="Master", zoom_record_sheet="Zoom Record",
        backup_sheet="Backup", cancel_sheet="Cancel", absen_sheet_id="ss_absen",
        absen_sheet_name="Absen", rekap_sheet_id="ss_rekap", rekap_bukti_folder_id=folder_parent,
        semester="1", reminder_slots=((11, 0),), reminder_enabled=False,
        heartbeat_hour=22, heartbeat_minute=0, heartbeat_enabled=False,
    )
    return sheets.SheetsClient(cfg)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def test_run_real(status):
    c = fresh_client()

    def boom():
        raise http_err(status)

    try:
        await c._run(boom)
        return None
    except sheets.SheetsError as e:
        return str(e)


async def test_bukti_folder(status):
    c = fresh_client()

    class FakeList:
        def execute(self):
            raise http_err(status, "Forbidden" if status == 403 else "Not Found")

    class FakeFiles:
        def list(self, **kw):
            return FakeList()

        def create(self, **kw):
            raise AssertionError("should not reach create")

    class FakeDrive:
        files = FakeFiles

    try:
        c._bukti_folder_for(FakeDrive(), "Rayhan")
        return None
    except sheets.SheetsError as e:
        return str(e)


for status in (403, 404, 429):
    msg = run(test_run_real(status))
    check(f"_run HttpError {status} -> SheetsError contains {status}", msg is not None and str(status) in msg, f"got {msg!r}")

for status, reason in ((403, "Forbidden"), (404, "Not Found")):
    msg = run(test_bukti_folder(status))
    check(f"_bukti_folder_for HttpError {status} -> SheetsError Drive:{status}",
          msg is not None and f"Drive: {status}" in msg and reason in msg, f"got {msg!r}")

# success path must still work: no folder parent -> clean SheetsError
c = fresh_client(folder_parent="")
try:
    class _D:  # unused
        pass
    c._bukti_folder_for(object(), "X")
    check("empty parent -> clean error", False, "no raise")
except sheets.SheetsError as e:
    check("empty parent -> clean error", "Folder Bukti belum diset" in str(e), f"got {e}")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    raise SystemExit(1)