"""Offline verification — /log picker grup bertingkat (kombinasi 1+2). HEAD after feature.

Checks:
  1. klasifikasi 3 grup: minggu ini personal hari-mendatang, backup tgl lama -> SEBELUMNYA,
     done -> SUDAH DI-LOG
  2. urut tanggal lama->baru dalam grup (backup Kamis 17 -> Sabtu 19 -> Minggu 20)
  3. done default TERSEMBUNYI + toggle '👁 (N) sudah di-log' muncul; vd:1 -> grup 3 tampil + hide
  4. semua kelas done -> 'Tidak ada kelas minggu ini' + toggle tetap ada
  5. indeks tombol c:{i} cocok ke posisi classes terurut (pick_class aman)
Run: py verify_zoom_picker_groups.py
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
import sheets
from config import Config
from handlers import log as logmod

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


def cls(code, day, category="Reguler", backup_hari_tanggal=""):
    return sheets.ClassEntry(
        code=code, subject=f"MK {code}", day=day, time_range="13.00 - 15.30",
        category=category, lecturer="Dosen", room="", rombel="", sks="3",
        zoom_number="", zoom_link="", backup_hari_tanggal=backup_hari_tanggal,
    )


# Fixed "today": Rabu 16 Sep 2026 WIB -> Senin=14/09, Minggu=20/09.
TODAY = datetime(2026, 9, 16, 12, 0, tzinfo=sheets.WIB)


def _fixed_last(day_name):
    norm = re.sub(r"[^a-z]", "", day_name.strip().lower())
    target = next((i for i, d in enumerate(sheets.DAY_ORDER) if re.sub(r"[^a-z]", "", d.lower()) == norm), None)
    if target is None:
        return sheets.today_str_wib()
    delta = (TODAY.weekday() - target) % 7
    return (TODAY - timedelta(days=delta)).strftime("%d/%m/%Y")


def _fixed_next(day_name):
    norm = re.sub(r"[^a-z]", "", day_name.strip().lower())
    target = next((i for i, d in enumerate(sheets.DAY_ORDER) if re.sub(r"[^a-z]", "", d.lower()) == norm), None)
    if target is None:
        return sheets.today_str_wib()
    delta = (target - TODAY.weekday()) % 7
    return (TODAY + timedelta(days=delta)).strftime("%d/%m/%Y")


logmod.sheets.last_date_for_day = _fixed_last
logmod.sheets.next_date_for_day = _fixed_next

# P-MON (Senin): last 14/09 -> done. P-SAT (Sabtu): last 12/09 < mon, next 19/09 in week (mendatang).
# P-SUN (Minggu): last 13/09 < mon, next 20/09 in week (mendatang).
# B-WEEK backup 17/09 in week. B-OLD backup 07/09 -> SEBELUMNYA.
CLASSES = [
    cls("P-MON", "Senin"),
    cls("P-SAT", "Sabtu"),
    cls("P-SUN", "Minggu"),
    cls("B-WEEK", "Kamis", category="Backup", backup_hari_tanggal="Kamis, 17 September 2026"),
    cls("B-OLD", "Senin", category="Backup", backup_hari_tanggal="Senin, 7 September 2026"),
]
DONE = {("p-mon", "14/09/2026")}

# 1. klasifikasi 3 grup
week, arrears, done = logmod._group_picker_classes(CLASSES, DONE, today=TODAY)
check("grup1 MINGGU INI: backup Kamis + personal hari-mendatang, urut tgl lama->baru",
      [c.code for c in week] == ["B-WEEK", "P-SAT", "P-SUN"],
      f"got {[c.code for c in week]}")
check("grup2 SEBELUMNYA: backup tgl lama (bukan done)",
      [c.code for c in arrears] == ["B-OLD"], f"got {[c.code for c in arrears]}")
check("grup3 SUDAH DI-LOG: personal yg done",
      [c.code for c in done] == ["P-MON"], f"got {[c.code for c in done]}")

# 2. label lama + header teks non-interaktif + done tersembunyi default
ordered = week + arrears + done
text, kb = logmod._build_class_view(ordered, DONE, show_done=False, today=TODAY)
labels = [b.text for row in kb for b in row]
check("label lama personal: kode — matkul (hari jam)",
      "P-SAT — MK P-SAT (Sabtu 13.00 - 15.30)" in labels, f"got {labels}")
check("label backup lama: 🔄 + tanggal eksplisit",
      "🔄 B-OLD — MK B-OLD (Senin, 7 September 2026)" in labels, f"got {labels}")
check("header grup = teks, bukan tombol",
      "— 📅 MINGGU INI —" in text and "— ⏳ SEBELUMNYA (belum di-log) —" in text,
      f"got {text!r}")
check("done default tersembunyi: tombol terakhir aksi = B-OLD (bukan P-MON)",
      any(b.callback_data.startswith("c:") and "B-OLD" in b.text for row in kb for b in row)
      and not any("P-MON" in b.text for row in kb for b in row),
      f"got {labels}")

# 3. toggle
toggle = [b for row in kb for b in row if b.callback_data == "vd:1"]
check("toggle '👁 (1) sudah di-log' muncul saat ada done",
      len(toggle) == 1 and toggle[0].text == "👁 (1) sudah di-log", f"got {[b.text for b in toggle]}")
text2, kb2 = logmod._build_class_view(ordered, DONE, show_done=True, today=TODAY)
check("toggle on: grup3 tampil + label done ✅ + tombol hide",
      "— ✅ SUDAH DI-LOG —" in text2
      and any("✅ P-MON" in b.text for row in kb2 for b in row)
      and any(b.callback_data == "vd:0" for row in kb2 for b in row),
      f"got text={text2!r}")

# 4. semua done -> 'Tidak ada kelas minggu ini' + toggle tetap
text3, kb3 = logmod._build_class_view([cls("P-MON", "Senin")], DONE, show_done=False, today=TODAY)
check("semua kelas done: pesan kosong + toggle tetap ada",
      "Tidak ada kelas minggu ini" in text3
      and any(b.callback_data == "vd:1" for row in kb3 for b in row)
      and any(b.callback_data == "back:cancel" for row in kb3 for b in row),
      f"got text={text3!r}")

# 5. indeks tombol -> posisi classes terurut (show on: semua item punya tombol)
ordered2 = week + arrears + done
text4, kb4 = logmod._build_class_view(ordered2, DONE, show_done=True, today=TODAY)
mapping = {}
for row in kb4:
    for b in row:
        if b.callback_data.startswith("c:"):
            mapping[int(b.callback_data.split(":")[1])] = b.text
ok = all(f"{ordered2[i].code}" in mapping.get(i, "") for i in range(len(ordered2)))
check("indeks tombol c:{i} cocok ke classes terurut",
      ok and set(mapping) == set(range(len(ordered2))), f"got {mapping}")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)