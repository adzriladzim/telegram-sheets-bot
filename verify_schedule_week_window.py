"""Self-check: /schedule week-window filter (sheets.this_week_classes).

Bug: _fetch_backup_classes memulangkan semua baris cocok nama tanpa cek tanggal,
this_week_classes group by nama hari saja, tanpa window -> backup "Selasa, 15
September 2026" tampil di Selasa 20 Sep. Fix: Backup/Make-up dengan
backup_hari_tanggal eksplisit hanya tampil kalau jatuh di minggu WIB berjalan.

Test hari: Senin 2026-09-21 (WIB) -> window Senin 21 .. Minggu 27 Sep 2026.
"""
from datetime import datetime
from types import SimpleNamespace

import sheets

WIB = sheets.WIB
TODAY = datetime(2026, 9, 21, 12, 0, tzinfo=WIB)  # Senin

def cls(code, day, category="Reguler", backup_hari_tanggal=""):
    return sheets.ClassEntry(
        code=code, subject=f"MK {code}", day=day, time_range="10.00 - 12.00",
        category=category, lecturer="Dosen", room="R", rombel="", sks="3",
        zoom_number="", zoom_link="", backup_hari_tanggal=backup_hari_tanggal)

CASES = [
    cls("REG", "Senin"),                                          # personal reguler -> selalu tampil
    cls("B-TODAY", "Senin", category="Backup", backup_hari_tanggal="Senin, 21 September 2026"),
    cls("B-OLD", "Selasa", category="Backup", backup_hari_tanggal="Selasa, 15 September 2026"),
    cls("B-FUT", "Kamis", category="Backup", backup_hari_tanggal="Kamis, 1 Oktober 2026"),
    cls("B-SUN", "Minggu", category="Backup", backup_hari_tanggal="Minggu, 20 September 2026"),
    cls("B-BAD", "Jumat", category="Backup", backup_hari_tanggal="entahlah??"),
    cls("M-TODAY", "Selasa", category="Make-up", backup_hari_tanggal="Selasa, 22 September 2026"),
    cls("M-OLD", "Rabu", category="Make-up", backup_hari_tanggal="Rabu, 16 September 2026"),
]

by_day = sheets.this_week_classes(CASES, TODAY)
flat = [c.code for lst in by_day.values() for c in lst]

def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        raise SystemExit(1)

print(f"window: {sheets.week_span_wib(TODAY)} | hari: {by_day.keys()}")
check("backup 15 Sep TIDAK tampil di minggu 20 Sep", "B-OLD" not in flat, f"flat={flat}")
check("backup 20 Sep (Minggu sblm window) TIDAK tampil", "B-SUN" not in flat)
check("backup 1 Okt (minggu depan) TIDAK tampil", "B-FUT" not in flat)
check("makeup 16 Sep TIDAK tampil", "M-OLD" not in flat)
check("backup HARI INI (21 Sep) tampil", "B-TODAY" in flat and "B-TODAY" in [c.code for c in by_day["Senin"]])
check("makeup besok (22 Sep, dalam window) tampil", "M-TODAY" in flat and "M-TODAY" in [c.code for c in by_day["Selasa"]])
check("personal reguler selalu tampil", "REG" in flat)
check("backup tanggal tak bisa di-parse tetap tampil (fail-open)", "B-BAD" in flat)
print("OK — semua check lolos")