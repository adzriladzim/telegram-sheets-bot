"""Stub verify: /start + /help copy rewrite (TelefasilBot, semua fitur, ajak aksi).
Run: py verify_start_copy.py   (expect: ALL PASS)
"""
from __future__ import annotations
import sys
import handlers.start as start

FAIL = []


def check(label, cond, msg=""):
    if not cond:
        FAIL.append(label)
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))


HELP_TXT = start.HELP.replace('"', "'")
START_TXT = start.start_cmd.__globals__  # inspect source strings below via source re-read
import inspect
SRC = inspect.getsource(start)

# /help copy
check("help sebut semua fitur", all(f"• /{w}" in HELP_TXT for w in
      ("zoom", "absen", "rekap", "backup", "cancel", "schedule", "register", "help")))
check("help timed out telegram", "/start — mulai ulang bot" not in HELP_TXT
      and "/zoom — isi form" not in HELP_TXT, "old scaffolding copy gone")
check("help sertakan notif otomatis", "Notif otomatis" in HELP_TXT and "WIB" in HELP_TXT)
check("help brand TelefasilBot", "<b>TelefasilBot</b>" in HELP_TXT)

# /start copy
check("start sapaan halo + nama", "Halo" in SRC and "TelefasilBot" in SRC)
check("start sebut semua fitur main", all(k in SRC for k in
      ("catat ", "absen", "rekap", "backup", "jadwal")))
check("start ajak aksi 1 langkah", "kirim /zoom" in SRC and "mulai mencatat" in SRC)
check("start tetap tombol", "reply_markup=_keyboard()" in SRC)

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL"))
sys.exit(1 if FAIL else 0)