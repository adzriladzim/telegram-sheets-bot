"""Offline verify — audit batch S2/S3 telegram-sheets-bot (no creds, no network).

Checks:
  1. _chat_locks bounded: >64 entri -> prune hanya lock yang free (locked dipertahankan)
  2. _classes_cache evict per-fasil: delegate/swap evict hanya fasil terlibat,
     fasil lain tetap cache; global clear TIDAK dipakai lagi
  3. _swap_fasil_names membaca Dicatat oleh (idx2) + Tukar dengan (idx5)
  4. parse_backup_date: 'Senin, 8 September 2026' -> '08/09/2026';
     dd/mm/yyyy pass-through; tak ter-parse -> today + log.warning (bukan silent)
  5. _norm_date: pakai ID_MONTHS (bukan dict inline) — hasil sama
  6. Config defaults: admin_ids=(2061872254,), feedback_ss_id/tab; env ADMIN_IDS overrides
  7. log._active_log_state: aktif -> state; none/END/TIMEOUT -> None;
     LOG_CONV None -> None (stub/verify aman)
  8. class_done_key: backup pakai eksplisit, personal pakai last_date_for_day
Run: py scripts/verify/verify_s2s3_audit.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
sys.stdout.reconfigure(encoding="utf-8")

import sheets
from config import Config, _int_list
from handlers import log as h_log

PASS, FAIL = [], []


def check(label, cond, msg=""):
    (PASS if cond else FAIL).append((label, msg))
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))


def cfg_fixture() -> Config:
    return Config(
        bot_token="x", sheet_id="ss_main", service_account_json=Path("secrets/none.json"),
        facilitator_name="", master_sheet="Master", zoom_record_sheet="Zoom Record",
        backup_sheet="Backup", cancel_sheet="Cancel", absen_sheet_id="ss_absen",
        absen_sheet_name="Absen", rekap_sheet_id="ss_rekap", rekap_bukti_folder_id="",
        semester="1", reminder_slots=((21, 0), (5, 0), (13, 0)), reminder_enabled=False,
        heartbeat_hour=22, heartbeat_minute=0, heartbeat_enabled=False,
    )


C = sheets.SheetsClient(cfg_fixture())

# ---------- 1. chat lock prune ----------
sheets._MAX_CHAT_LOCKS = 4  # kecilkan ambang utk test tanpa 64 entri
for i in range(8):
    C._chat_locks[i] = asyncio.Lock()
C._chat_locks[9] = asyncio.Lock()


async def _prune_case():
    async with C._chat_locks[3]:
        async with C._chat_locks[5]:
            C._prune_chat_locks()  # saat keduanya dipegang -> keduanya tak boleh di-pop
    C._prune_chat_locks()          # setelah dilepas -> prune free sisanya
    return sorted(C._chat_locks.keys())


left = asyncio.run(_prune_case())
check("prune: lock yg lagi dipegang (3,5) dipertahankan", 3 in left and 5 in left, f"left={left}")
check("prune: lock free di-pop (size <= ambang)", len(left) <= sheets._MAX_CHAT_LOCKS, f"left={left}")
C._chat_locks.clear()
sheets._MAX_CHAT_LOCKS = 64

# ---------- 2. evict per-fasil ----------
def _seed(cache):
    cache.clear()
    for k in ("fasil a", "fasil b", "fasil c"):
        cache[k] = (0.0, [k])

_seed(sheets._classes_cache)
C._evict_classes("Fasil A", "Fasil B")
check("evict: fasil terlibat ter-evict", "fasil a" not in sheets._classes_cache and "fasil b" not in sheets._classes_cache,
      f"got {sorted(sheets._classes_cache)}")
check("evict: fasil lain tetap cache", "fasil c" in sheets._classes_cache, f"got {sorted(sheets._classes_cache)}")
C._evict_classes("")  # no-op harus aman
C._evict_classes(None) if False else None  # type guard: only str

_seed(sheets._classes_cache)
C._evict_classes("Fasil C")
check("evict: satu fasil saja", len(sheets._classes_cache) == 2 and "fasil c" not in sheets._classes_cache,
      f"got {sorted(sheets._classes_cache)}")

# ---------- 3. _swap_fasil_names ----------
row = ["1", "2026-09-20 10:00", "Fasil A", "X1", "21/09/2026", "Fasil B", "Y1", "sekali", "13.00", "AKTIF", ""]
oleh, dengan = C._swap_fasil_names(row)
check("swap fasil names: oleh + dengan", (oleh, dengan) == ("Fasil A", "Fasil B"), f"got {(oleh, dengan)}")
check("swap fasil names: row pendek aman", C._swap_fasil_names([]) == ("", ""))

# ---------- 4. parse_backup_date ----------
check("parse_backup_date normal", sheets.parse_backup_date("Senin, 8 September 2026") == "08/09/2026")
check("parse_backup_date hari kosong (single token)", sheets.parse_backup_date("8 September 2026") == "08/09/2026")
check("parse_backup_date dd/mm/yyyy pass-through", sheets.parse_backup_date("08/09/2026") == "08/09/2026")

import logging as _logging
_warns = []
_h = _logging.Handler()


class _Rec(_logging.Handler):
    def emit(self, record):
        _warns.append(record.getMessage())


rh = _Rec()
_lg = _logging.getLogger("sheets")
_lg.addHandler(rh)
_lg.setLevel(_logging.WARNING)
try:
    got = sheets.parse_backup_date("goreng")
finally:
    _lg.removeHandler(rh)
check("parse_backup_date gagal -> today + warn", got == sheets.today_str_wib() and len(_warns) == 1,
      f"got={got} warns={_warns}")

# ---------- 5. _norm_date memakai ID_MONTHS ----------
check("_norm_date long form", C._norm_date("Senin, 8 September 2026") == "08/09/2026")
check("_norm_date dd/mm/yyyy", C._norm_date("08/09/2026") == "08/09/2026")
check("_norm_date unparseable -> apa adanya", C._norm_date("goreng") == "goreng")
check("_norm_date bulan tak dikenal -> 01 (perilaku lama)", C._norm_date("8 Zorbuary 2026") == "08/01/2026")

# ---------- 6. Config defaults + env ----------
cfg = cfg_fixture()
check("config default admin_ids", cfg.admin_ids == (2061872254,), str(cfg.admin_ids))
check("config default feedback_ss_id", cfg.feedback_ss_id == "1dZQcq3TvPh7wkW0z8SF94YExs5jONYf_O3oV09Hk604", cfg.feedback_ss_id)
check("config default feedback_tab", cfg.feedback_tab == "Form Responses 1", cfg.feedback_tab)
old = os.getenv("ADMIN_IDS")
os.environ["ADMIN_IDS"] = "111, 222"
try:
    check("config env ADMIN_IDS parse", _int_list("ADMIN_IDS", "2061872254") == (111, 222), str(_int_list("ADMIN_IDS", "2061872254")))
finally:
    if old is None:
        os.environ.pop("ADMIN_IDS", None)
    else:
        os.environ["ADMIN_IDS"] = old

# ---------- 7. _active_log_state guard ----------
class _FakeUpdate:
    def __init__(self, chat, user):
        self.effective_chat = type("C", (), {"id": chat})()
        self.effective_user = type("U", (), {"id": user})()
        self.effective_message = object()
        self.callback_query = None


class _FakeConv:
    _conversations = {}


orig_conv = h_log.LOG_CONV
try:
    check("guard: LOG_CONV None -> None", h_log._active_log_state(_FakeUpdate(1, 1), None) is None)
    _FakeConv._conversations = {(1, 1): h_log.MEETING}
    h_log.LOG_CONV = _FakeConv
    check("guard: conv aktif -> state MEETING", h_log._active_log_state(_FakeUpdate(1, 1), None) == h_log.MEETING,
          str(h_log._active_log_state(_FakeUpdate(1, 1), None)))
    _FakeConv._conversations = {(1, 1): h_log.ConversationHandler.END}
    check("guard: state END -> None", h_log._active_log_state(_FakeUpdate(1, 1), None) is None)
    _FakeConv._conversations = {}
    check("guard: tanpa entri -> None", h_log._active_log_state(_FakeUpdate(1, 1), None) is None)
finally:
    h_log.LOG_CONV = orig_conv

# ---------- 8. class_done_key ----------
def _cls(cat, backup=""):
    return sheets.ClassEntry(
        code="CS101", subject="Algo", day="Selasa", time_range="19.00 - 21.00", category=cat,
        lecturer="D", room="R1", rombel="", sks="3", zoom_number="", zoom_link="",
        backup_hari_tanggal=backup)


check("class_done_key backup eksplisit", sheets.class_done_key(_cls("Backup", "Senin, 8 September 2026")) == ("cs101", "08/09/2026"))
check("class_done_key backup tanpa tanggal -> ('', kosong)",
      sheets.class_done_key(_cls("Make-up", "")) == ("cs101", ""))
orig = sheets.last_date_for_day
sheets.last_date_for_day = lambda d: "22/09/2026"
try:
    check("class_done_key personal -> last date", sheets.class_done_key(_cls("Reguler")) == ("cs101", "22/09/2026"))
finally:
    sheets.last_date_for_day = orig

print(f"\nRESULT PASS={len(PASS)} FAIL={len(FAIL)}")
sys.exit(1 if FAIL else 0)