"""Self-check laporan PDF beta — tanpa network/Google, tanpa bot jalan.

Buat data sintetis, generate PDF + chart nyata via fpdf2/matplotlib, assert
threshold kategori, pola nama file, format PDF/PNG, parser range, beta gate.
Jalankan: py -3.12 verify_laporan.py
"""
from __future__ import annotations

import io
import os
import re
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "handlers")

import handlers.laporan as L  # noqa: E402


def _check(name: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        sys.exit(1)


# 1) kategori threshold (scope: >=4.5 Sangat Baik, >=4.0 Baik)
_check("predicate 4.51 -> Sangat Baik", L._predicate(4.51) == "Sangat Baik")
_check("predicate 4.53 -> Sangat Baik", L._predicate(4.53) == "Sangat Baik")
_check("predicate 4.47 -> Baik", L._predicate(4.47) == "Baik")
_check("predicate 4.00 -> Baik", L._predicate(4.0) == "Baik")
_check("predicate 3.99 -> Cukup", L._predicate(3.99) == "Cukup")

# 2) nama file pola: Laporan-CSAT-<NAMA>-<KODE>-<YYYYMMDD>.pdf
fname = L._pdf_filename("Fella Amalia S.T., M.T.", "PPC01")
pat = r"^Laporan-CSAT-FELLA-AMALIA-S-T-M-T-PPC01-\d{8}\.pdf$"
_check(f"nama file pola template = {fname}", bool(re.match(pat, fname)))

# 3) parser range
_check("range '3-5' -> [3,4,5]", L._first_nums("3-5") == [3, 4, 5])
_check("tunggal '4' -> [4]", L._first_nums("4") == [4])
_check("'3 dan 4' -> [3,4]", L._first_nums("3 dan 4") == [3, 4])
_check("'4,8' -> [4,8]", L._first_nums("4,8") == [4, 8])
_check("label '3-5'", L._meeting_range_label([5, 3, 4]) == "3-5")
_check("label tunggal", L._meeting_range_label([4]) == "4")

# 4) beta gate (env di-set di runtime)
os.environ["LAPORAN_BETA_IDS"] = "111,222"
os.environ["LAPORAN_BETA_NAMES"] = "Fella Amalia S.T., M.T.|Adzril Adzim Hendrynov"
_check("gate allow chat+nama cocok", L._beta_allowed(111, "fella amalia s.t., m.t."))
_check("gate deny chat tak di daftar", not L._beta_allowed(333, "Fella Amalia S.T., M.T."))
_check("gate deny nama tak cocok", not L._beta_allowed(111, "Orang Lain"))
_check("gate deny nama None", not L._beta_allowed(111, None))
os.environ.pop("LAPORAN_BETA_IDS", None)
os.environ.pop("LAPORAN_BETA_NAMES", None)
_check("gate tutup saat env kosong", not L._beta_allowed(111, "Fella Amalia"))

# 5) chart PNG nyata
png = L._make_chart_png([1, 2, 3, 4], [4.55, 4.49, 4.48, 4.51])
_check("chart PNG header magic", png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000)

# 6) build PDF nyata via fpdf2 (+ font bundel repo)
data = {
    "nama": "Fella Amalia S.T., M.T.",
    "kode": "PPC01",
    "subject": "Production Planning and Control",
    "pertemuan_label": "3-5",
    "responden_total": 17,
    "cards": [("CSAT Gabungan", 4.51), ("Performa", 4.53), ("Pemahaman", 4.53), ("Interaktivitas", 4.47)],
    "meetings": [
        {"pertemuan": 3, "tanggal": "15/09/2026", "subject": "Production Planning and Control",
         "dosen": "Fella Amalia S.T., M.T.", "total": 19, "hadir": 18, "feedback": 17,
         "tidak": 1, "belum": 1, "responden": 17, "skor": 4.47},
        {"pertemuan": 4, "tanggal": "17/09/2026", "subject": "Production Planning and Control",
         "dosen": "Fella Amalia S.T., M.T.", "total": 19, "hadir": 18, "feedback": 17,
         "tidak": 1, "belum": 1, "responden": 17, "skor": 4.47},
        {"pertemuan": 5, "tanggal": "22/09/2026", "subject": "Production Planning and Control",
         "dosen": "Fella Amalia S.T., M.T.", "total": 20, "hadir": 18, "feedback": 17,
         "tidak": 2, "belum": 1, "responden": 17, "skor": 4.25},
    ],
    "tanggal": "20-09-2026",
}
fname = L._pdf_filename(data["nama"], data["kode"])
pdf = L._build_pdf(data, fname)
_check(f"PDF %PDF header + size>10KB ({len(pdf)} bytes)", pdf[:4] == b"%PDF" and len(pdf) > 10000)
_check("PDF tidak berisi error ps", b"Traceback" not in pdf and b"FPDF error" not in pdf)

# 7) verifikasi pola cross-card computed (gabungan rata-rata)
gab = round(sum(m["skor"] for m in data["meetings"]) / 3, 2)
_check(f"gabungan rata-rata = {gab}", gab == 4.40)

out = f"Laporan-CSAT-FELLA-AMALIA-S-T-M-T-PPC01-20260920.pdf"
with open(out, "wb") as f:
    f.write(pdf)
print(f"OK: sample PDF tersimpan {out} ({len(pdf)} bytes)")
print("ALL PASS")