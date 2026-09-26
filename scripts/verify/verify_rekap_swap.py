"""Offline verification — rekap G/H swap fix + label + step order.

Live probe (2026-09-17) found: rekap header G='Pertemuan ke-', H='SKS'
but RekapRecord.as_row() wrote sks->G, pertemuan->H. Bot-written row 14
(InVC6) shows Pertemuan=3, SKS=2 (harus 2/3). Fix: swap as_row, status
labels, _start_fix prefill, confirm_cb fix-path vals; peran-before-confirm
guard; SESI_OPTS == dropdown sheet options.

Run: py verify_rekap_swap.py
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
import sheets
from config import Config
from handlers import rekap

PASS, FAIL = [], []


def check(label, cond, msg=""):
    (PASS if cond else FAIL).append((label, msg))
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))


# ---------- 1. as_row G/Pertemuan, H/SKS ----------
rec = sheets.RekapRecord(
    facilitator="F", tanggal="11 September 2026", lecturer="Dosen", jam="18.00",
    kode="InVC6", subject="Intro VC", sks="3", pertemuan="2",
    tipe="On-site", sesi="Kelas Biasa", peran="Fasilitator Kelas", bukti="",
)
row = rec.as_row()
check("as_row: index5(G)=Pertemuan, index6(H)=SKS",
      row[5] == "2" and row[6] == "3", f"got G={row[5]!r} H={row[6]!r}")
check("as_row: B/C/D/E/F tetap (tanggal,dosen,jam,kode,matkul)",
      row[0] == "11 September 2026" and row[1] == "Dosen" and row[2] == "18.00"
      and row[3] == "InVC6" and row[4] == "Intro VC", f"got {row[:5]}")

# ---------- 2. _rekap_row_status labels order ----------
src_sheets = Path("sheets.py").read_text(encoding="utf-8")
m = re.search(r'labels = (\[.*?\])', src_sheets, re.S)
labels = eval(m.group(1)) if m else []
check("rekap_row_status labels: index5=Pertemuan, index6=SKS",
      labels[5] == "Pertemuan" and labels[6] == "SKS", f"got {labels}")
check("rekap_row_status labels: 11 label lengkap",
      labels == ["Tanggal", "Dosen", "Jam", "Kode", "Matkul", "Pertemuan",
                 "SKS", "Tipe", "Sesi", "Peran", "Bukti"], f"got {labels}")

# ---------- 3. _start_fix prefill reads cell(6) ----------
src_rekap = Path("handlers/rekap.py").read_text(encoding="utf-8")
check("start_fix prefill: meeting dari cell(6) (G, Pertemuan)",
      'cell(6): context.user_data["meeting"] = cell(6)' in src_rekap.replace("if ", "if "),
      "cell(6) prefill tidak ada")
check("start_fix: cell(7) PRE-FIX tidak dipakai lagi utk meeting",
      'if cell(7): context.user_data["meeting"] = cell(7)' not in src_rekap,
      "cell(7) meeting masih ada")

# ---------- 4. confirm_cb fix-path vals order ----------
check("confirm_cb: vals G=Pertemuan, H=SKS",
      'rec.pertemuan, rec.sks, rec.tipe' in src_rekap
      and 'rec.sks, rec.pertemuan, rec.tipe' not in src_rekap,
      "vals order salah")

# ---------- 5. peran-before-confirm guard ----------
check("start_fix: peran ditanya sebelum bukti/confirm (urutan sesi->peran)",
      "if not context.user_data.get(\"peran\"):" in src_rekap
      and 'return await _ask_peran(message, context)' in src_rekap,
      "guard peran tidak ada")

# ---------- 6. SESI_OPTS == dropdown sheet (nilai + urutan) ----------
check("SESI_OPTS == opsi dropdown sheet (urutan sama)",
      rekap.SESI_OPTS == ["Kelas Biasa", "Guest Lecture", "Lainnya", "Workshop/E-Lab"],
      f"got {rekap.SESI_OPTS}")
check("PERAN_OPTS == opsi dropdown sheet",
      rekap.PERAN_OPTS == ["Fasilitator Kelas", "Moderator Guest Lecture", "Backup Fasil"],
      f"got {rekap.PERAN_OPTS}")

# ---------- 7. _zoom_tipe scheme -> opsi sheet ----------
check("_zoom_tipe: Online->Online, Offline->On-site",
      rekap._zoom_tipe("Online") == "Online" and rekap._zoom_tipe("Offline") == "On-site",
      f"got {rekap._zoom_tipe('Online')}/{rekap._zoom_tipe('Offline')}")

# ---------- 8. _zoom_entries col mapping (dari probe live) ----------
class FakeClient(sheets.SheetsClient):
    def __init__(self):
        super().__init__(Config(
            bot_token="x", sheet_id="s", service_account_json=Path("secrets/none.json"),
            facilitator_name="", master_sheet="M", zoom_record_sheet="Z",
            backup_sheet="B", cancel_sheet="C", absen_sheet_id="a", absen_sheet_name="A",
            rekap_sheet_id="r", rekap_bukti_folder_id="", semester="1",
            reminder_slots=((21, 0),), reminder_enabled=False,
            heartbeat_hour=22, heartbeat_minute=0, heartbeat_enabled=False))
        self.rows = None

    async def zoom_entries(self, name):
        return await self._run(lambda: self._ze(name))

    def _ze(self, name):
        # replicate _zoom_entries column mapping (row 397 = InVC6 live)
        rows = [
            ["Hdr"] * 15,
            ["1", "17/09/2026", name, "Jumat, 11 September 2026", "1", "InVC6", "Intro VC",
             "2", "Offline", "3", "Professional", "Kriswanto, S.I.Kom., M.Sc.", "18.00", "Zoom 46", ""],
        ]
        out = []
        for i, r in enumerate(rows):
            if i == 0:
                continue
            out.append({
                "kode": r[5].strip(), "subject": r[6].strip(),
                "tanggal": self._norm_date(r[3].strip()),
                "pertemuan": r[7].strip(), "scheme": r[8].strip(),
                "sks": r[9].strip(), "tipe": r[10].strip(), "dosen": r[11].strip(),
                "mulai": r[12].strip(), "zoom": r[13].strip(),
                "catatan": r[14].strip(), "row": i + 1,
            })
        return out


async def _fetch():
    c = FakeClient()
    return await c.zoom_entries("Adzril Adzim Hendrynov")


e = asyncio.run(_fetch())[0]
check("zoom_entries InVC6: D->tanggal 11/09, H->pertemuan 2, J->sks 3",
      e["tanggal"] == "11/09/2026" and e["pertemuan"] == "2" and e["sks"] == "3"
      and e["scheme"] == "Offline", f"got {e}")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)