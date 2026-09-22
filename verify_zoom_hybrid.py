"""Offline verification — hybrid picker pertemuan /zoom.

Cek:
  1. default pintar LANJUT LOGIS gap-min: ganjil/genap 2 fasil, barisan tak urut,
     gap P2 -> saran 2 (bukan max+1 = 4)
  2. kontigu P1,P2,P3 -> saran 4 (max+1 tetap benar)
  3. dupe gate (kode, tanggal, pertemuan): P1 dobel ditolak + lapor siapa perekam;
     overlap '3' vs '3 dan 4' kena
  4. backup P3 tak geser progres kelas: P1,P2 reguler + P3 backup -> saran 3 (bukan 4)
  5. pola #data/pola.json: warning 🟡 saat fasil diluar pola, kosong tanpa config,
     cocok -> tanpa warning
  6. picker pertemuan SELALU tampil (pick_class -> MEETING, bukan auto-jump SKEMA) +
     allow_reentry + handler dupe gate terpasang
  7. make-up read-only filter J!=true tetap (source guard)
Run: py verify_zoom_hybrid.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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


# Layout get_all_values: B=tgl isi(1) C=fasil(2) D=tgl kelas(3) E=sem(4) F=kode(5)
# G=mk(6) H=pertemuan(7) I=scheme(8) J=sks(9) K=tipe(10) L=dosen(11) M=mulai(12)
# N=zoom(13) O=catatan(14)
def zrow(fasil, tgl, kode, pert, tipe="Reguler"):
    r = [""] * 15
    r[1], r[2], r[3], r[5], r[7], r[10] = "01/09/2026", fasil, tgl, kode, pert, tipe
    return r


class FakeClient(sheets.SheetsClient):
    """No gspread I/O — _cached_rows dikembalikan dari fixture utk tab Zoom."""

    def __init__(self, zoom_rows):
        super().__init__(cfg_fixture())
        self._zoom_rows = zoom_rows

    def _cached_rows(self, ss_id, title):
        if ss_id == self.cfg.sheet_id and title == self.cfg.zoom_record_sheet:
            return self._zoom_rows
        return []


def _run(fn):
    async def _a():
        return await fn()
    return asyncio.run(_a())


# ---------- 1. ganjil/genap tak urut -> gap-min ----------
# 2 fasil: A rekam P1 & P3 (barisan tak urut: P3 fisik duluan), B belum rekam P2
# (gap). max+1 lama = 4 (melompati P2); lanjut logis = 2.
ROWS_GAP = [
    ["Hdr"] * 15,
    zrow("Fasil A", "06/09/2026", "PPC01", "3"),   # fisik duluan walau angka besar
    zrow("Fasil A", "30/08/2026", "PPC01", "1"),
]
fc = FakeClient(ROWS_GAP)
last, nxt, nxt2 = _run(lambda: fc.get_next_meeting("PPC01"))
check("gap-min: tak urut P3,P1 (P2 kosong) -> saran 2 bukan max+1=4",
      nxt == "2" and nxt2 == "2 dan 3", f"got nxt={nxt!r} nxt2={nxt2!r}")
check("gap-min: last = pertemuan max (3)",
      last == "3", f"got last={last!r}")

# ---------- 2. kontigu -> max+1 benar
ROWS_FULL = [
    ["Hdr"] * 15,
    zrow("Fasil A", "30/08/2026", "PPC02", "1"),
    zrow("Fasil B", "06/09/2026", "PPC02", "2 dan 3"),
]
last2, nxt2o, nxt22 = _run(lambda: FakeClient(ROWS_FULL).get_next_meeting("PPC02"))
check("kontigu 1,2,3 -> saran 4",
      nxt2o == "4" and nxt22 == "4 dan 5", f"got {nxt2o!r} {nxt22!r}")

# ---------- 3. dupe gate ----------
ROWS_DUPE = [
    ["Hdr"] * 15,
    zrow("Ratu Bilqis", "08/09/2026", "PPC01", "1"),
]
fdup = FakeClient(ROWS_DUPE)
found = _run(lambda: fdup.find_conflicts("PPC01", "08/09/2026", "1"))
check("dupe gate: key sama (kode,tanggal,ptm) -> ketemu rekaman",
      len(found) == 1 and found[0]["fasil"] == "Ratu Bilqis" and found[0]["pertemuan"] == "1",
      f"got {found}")
report = logmod._conflict_report_text("PPC01", "08/09/2026", "1", found)
check("dupe gate: lapor siapa perekam + tawaran koreksi",
      "Ratu Bilqis" in report and "Pakai nomor lain" not in report and "koreksi" in report,
      f"got {report!r}")

ROWS_OVERLAP = [
    ["Hdr"] * 15,
    zrow("Fasil B", "08/09/2026", "PPC02", "3 dan 4"),
]
found2 = _run(lambda: FakeClient(ROWS_OVERLAP).find_conflicts("PPC02", "08/09/2026", "3"))
check("dupe gate: overlap '3' vs '3 dan 4' kena",
      len(found2) == 1, f"got {found2}")
found3 = _run(lambda: FakeClient(ROWS_OVERLAP).find_conflicts("PPC02", "08/09/2026", "5"))
check("dupe gate: pertemuan 5 (beda) tak konflik",
      len(found3) == 0, f"got {found3}")

# ---------- 4. backup P3 tak geser progres ----------
ROWS_BACKUP = [
    ["Hdr"] * 15,
    zrow("Fasil A", "30/08/2026", "PPC03", "1"),
    zrow("Fasil B", "06/09/2026", "PPC03", "2"),
    zrow("Fasil C", "13/09/2026", "PPC03", "3", tipe="Backup"),  # backup justru P3
]
last4, nxt4, _ = _run(lambda: FakeClient(ROWS_BACKUP).get_next_meeting("PPC03"))
check("backup P3 tak geser progres: P1,P2 reguler + backup P3 -> saran 3 (bukan 4)",
      nxt4 == "3", f"got nxt={nxt4!r} last={last4!r}")
check("last progres = max reguler (2), bukan backup",
      last4 == "2", f"got last={last4!r}")

# ---------- 5. pola ----------
tmpdir = Path(tempfile.mkdtemp(prefix="pola_"))
polafile = tmpdir / "pola.json"
polafile.write_text(
    '{"PPC01": {"pertemuan": {"1": "Fasil A", "2": "Fasil B", "3": "Fasil A", "4": "Fasil B"}}}',
    encoding="utf-8")
os.environ["POLA_JSON_PATH"] = str(polafile)
try:
    p_ok = logmod._pola_warning("Fasil A", "PPC01", "1")
    check("pola: fasil cocok (A utk ganjil) -> tanpa warning",
          p_ok == "", f"got {p_ok!r}")
    p_bad = logmod._pola_warning("Fasil A", "PPC01", "2")
    check("pola: fasil di luar pola (A utk genap) -> warning 🟡 dengan pelaku",
          "🟡" in p_bad and "Fasil B" in p_bad and "bukan blokir" in p_bad,
          f"got {p_bad!r}")
    p_noconf = logmod._pola_warning("Fasil A", "PPC99", "1")
    check("pola: kode tanpa config -> normal tanpa warning",
          p_noconf == "", f"got {p_noconf!r}")
    p_escape = logmod._pola_warning("Fasil A", "PPC01", "2")
    check("pola: html-escape nama fasil dari config",
          "<b>" in p_escape, f"got {p_escape!r}")
finally:
    os.environ.pop("POLA_JSON_PATH", None)

# ---------- 6. picker selalu tampil + wiring ----------
src = Path("handlers/log.py").read_text(encoding="utf-8")
step_src = src.split("async def _meeting_step")[1].split("async def")[0]
meeting_kb_src = ("_meeting_kb(nxt, nxt2)" in step_src
                  and "await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=_meeting_kb(nxt, nxt2))" in step_src
                  and "return MEETING" in step_src)
pick_src = src.split("async def pick_class")[1].split("async def")[0]
check("picker: pick_class kirim _meeting_kb + return MEETING (bukan auto-jump SKEMA)",
      meeting_kb_src and ("return await _meeting_step(q, context, c)" in pick_src or "return _meeting_step(q, context, c)" in pick_src),
      "meeting picker tidak dirender via _meeting_step")
check("date: pick_class kelas personal tampilkan pilihan tanggal (d:next/d:last) sebelum meeting",
      "async def pick_date" in src and "d:(next|last)" in src and "return DATE" in pick_src,
      "date step untuk kelas personal hilang")
check("allow_reentry=True terpasang di conv /log",
      re.search(r"allow_reentry\s*=\s*True", src) is not None, "allow_reentry tidak ketemu")
check("dupe gate: handler x:force + back:meeting di state CONFIRM",
      bool(re.search(r'CallbackQueryHandler\(confirm_force, pattern=r"\^x:force\$"\)', src))
      and bool(re.search(r'CallbackQueryHandler\(back_to_meeting, pattern=r"\^back:meeting\$"\)', src)),
      "handler dupe gate tidak terpasang di CONFIRM")

# ---------- 7. make-up read-only J!=true tetap ----------
ssrc = Path("sheets.py").read_text(encoding="utf-8")
jawab = re.search(r"if status in \(\"true\", \"t\"\):", ssrc)
check("make-up: filter J!=true (status true/t skip) tetap ada",
      jawab is not None, "guard J!=true hilang")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)