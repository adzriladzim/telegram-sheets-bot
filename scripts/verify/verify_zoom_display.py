"""Offline verification stub — Zoom display-name parser for _resolve_absen. HEAD c5eb2bb.

Checks:
  1. display-name exact "029_Ahmad Maulana_Prodi" → single contains match accepted
  2. multi-match | tiebreaker: NIM penuh endsWith 3 digit diprioritaskan
  3. multi-match tanpa digit cocok → tetap AMBIGU (tidak auto-tembak)
  4. NIM penuh exact tetap jalan
  5. nama plain contains tetap jalan
  6. format aneh `A_B_C` tidak kena regex → unmatched (bukan match)
Run: py verify_zoom_display.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
import sheets
from config import Config

PASS = []
FAIL = []


def check(label, cond, msg=""):
    (PASS if cond else FAIL).append((label, msg))
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))


def cfg_fixture():
    return Config(
        bot_token="x", sheet_id="ss_main", service_account_json=Path("secrets/none.json"),
        facilitator_name="", master_sheet="Master", zoom_record_sheet="Zoom Record",
        backup_sheet="Backup", cancel_sheet="Cancel", absen_sheet_id="ss_absen",
        absen_sheet_name="Absen", rekap_sheet_id="ss_rekap", rekap_bukti_folder_id="",
        semester="1", reminder_slots=((21, 0), (5, 0), (13, 0)), reminder_enabled=False,
        heartbeat_hour=22, heartbeat_minute=0, heartbeat_enabled=False,
    )


# (_locate_absen_block contract): list[(title, rows, nim_header)]
BLOCKS = [
    ("Ilkom", [
        ["Kode Kelas", "CS101"],
        ["NIM", "Nama", "Mode"],
        [],  # blank separator (real sheets convention: data starts nim_header+2)
        ["26111600029", "Ahmad Maulana", "O"],
        ["26111600007", "Dewi Lestari", "S"],
        ["26111600045", "Dewi Anggraini", "S"],
        ["Program Studi", "Ilmu Komputer"],
    ], 1),
]


def fresh():
    c = sheets.SheetsClient(cfg_fixture())
    c._locate_absen_block = lambda kode: BLOCKS  # no gspread I/O
    return c


c = fresh()

# 1. display-name exact
r = c._resolve_absen("CS101", ["029_Ahmad Maulana_If"])
check("display exact: nama+digit cocok → 1 matched",
      len(r["matched"]) == 1 and r["matched"][0][1] == "26111600029"
      and not r["ambiguous"] and not r["unmatched"], f"got {r}")

# 2. tiebreaker: core "Dewi" 2 hits, digit cuma 007 → Dewi Lestari
r = c._resolve_absen("CS101", ["007_Dewi_If"])
check("tiebreaker: NIM endsWith 3 digit menang",
      len(r["matched"]) == 1 and r["matched"][0][1] == "26111600007"
      and not r["ambiguous"] and not r["unmatched"], f"got {r}")

# 3. multi tanpa digit cocok → AMBIGU
r = c._resolve_absen("CS101", ["999_Dewi_SI"])
check("ambiguous tanpa digit cocok tetap warned",
      not r["matched"] and "999_Dewi_SI" in r["ambiguous"]
      and len(r["ambiguous"]["999_Dewi_SI"]) == 2, f"got {r}")

# 4. NIM penuh exact
r = c._resolve_absen("CS101", ["26111600029"])
check("NIM penuh exact tetap works",
      len(r["matched"]) == 1 and r["matched"][0][1] == "26111600029"
      and not r["ambiguous"] and not r["unmatched"], f"got {r}")

# 5. nama plain contains (2 hits → ambiguous, 1 hit → matched)
r = c._resolve_absen("CS101", ["Ahmad Maulana"])
check("nama plain contains single tetap works",
      len(r["matched"]) == 1 and r["matched"][0][1] == "26111600029", f"got {r}")
r = c._resolve_absen("CS101", ["Dewi"])
check("nama plain contains multi → ambiguous (perilaku lama)",
      not r["matched"] and "Dewi" in r["ambiguous"], f"got {r}")

# 6. format aneh A_B_C tidak kena regex
r = c._resolve_absen("CS101", ["A_B_C"])
check("format aneh A_B_C → unmatched, bukan match",
      not r["matched"] and r["unmatched"] == ["A_B_C"] and not r["ambiguous"], f"got {r}")

# 7. campuran: display + NIM + plain sekaligus
r = c._resolve_absen("CS101", ["029_Ahmad Maulana_If", "26111600007", "Dewi Anggraini"])
names = sorted(e[1] for e in r["matched"])
check("campuran display+NIM+plain → 3 matched",
      names == ["26111600007", "26111600029", "26111600045"]
      and not r["ambiguous"] and not r["unmatched"], f"got {r}")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)