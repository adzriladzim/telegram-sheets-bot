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

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
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
         "tidak": 1, "belum": 1, "responden": 17, "skor": 4.47,
         "skorPemahaman": 4.5, "skorInteraktif": 4.5, "skorPerforma": 4.5, "csat": 4.47},
        {"pertemuan": 4, "tanggal": "17/09/2026", "subject": "Production Planning and Control",
         "dosen": "Fella Amalia S.T., M.T.", "total": 19, "hadir": 18, "feedback": 17,
         "tidak": 1, "belum": 1, "responden": 17, "skor": 4.47,
         "skorPemahaman": 4.5, "skorInteraktif": 4.5, "skorPerforma": 4.5, "csat": 4.47},
        {"pertemuan": 5, "tanggal": "22/09/2026", "subject": "Production Planning and Control",
         "dosen": "Fella Amalia S.T., M.T.", "total": 20, "hadir": 18, "feedback": 17,
         "tidak": 2, "belum": 1, "responden": 17, "skor": 4.25,
         "skorPemahaman": 4.2, "skorInteraktif": 4.3, "skorPerforma": 4.25, "csat": 4.25},
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

_CHECK = _check  # alias utk async test pakai asyncio

out = f"Laporan-CSAT-FELLA-AMALIA-S-T-M-T-PPC01-20260920.pdf"
with open(out, "wb") as f:
    f.write(pdf)
print(f"OK: sample PDF tersimpan {out} ({len(pdf)} bytes)")
# ===== skor rating CSAT (fix: mean rating 1-5, BUKAN 5*responden_rate) =====
import asyncio  # noqa: E402

import sheets  # noqa: E402

# 8) _parse_rating: Likert '(N) ...' -> N.0, fallback angka, None bila bukan angka
_check("parse '(5) Saya memahami...' -> 5.0", sheets._parse_rating("(5) Saya memahami seluruh materi") == 5.0)
_check("parse '(4) ...' -> 4.0", sheets._parse_rating("(4) Keberjalanan kelas cukup menarik") == 4.0)
_check("parse '(3)' -> 3.0", sheets._parse_rating("(3) teks") == 3.0)
_check("parse '2' -> 2.0", sheets._parse_rating("2") == 2.0)
_check("parse teks tanpa angka -> None", sheets._parse_rating("sangat baik") is None)
_check("parse kosong -> None", sheets._parse_rating("") is None)

# 9) _gather_report_data: skor per pertemuan = mean rating (mis. 60% responden
#    dgn rating 4-5 -> skor ~4.x, BUKAN 5.0*0.6=3.0)
class _FakeSheets:
    """Fake sync SheetsClient utk _gather_report_data (async wrapper di-skip)."""

    def __init__(self):
        self.entries = [
            {"kode": "PPC01", "pertemuan": "3", "tanggal": "15/09/2026",
             "dosen": "Fella Amalia S.T., M.T.", "subject": "Production Planning and Control"},
            {"kode": "PPC01", "pertemuan": "4", "tanggal": "17/09/2026",
             "dosen": "Fella Amalia S.T., M.T.", "subject": "Production Planning and Control"},
        ]

    async def zoom_entries(self, name):
        return self.entries

    async def find_rekap_tab(self, name):
        return ""

    async def rekap_rows(self, tab):
        return []

    async def absen_counts(self, kode, p):
        return {"total": 20, "hadir": 18, "feedback": 12, "prodi": ""}

    async def feedback_counts(self, kode, pertemuan, dosen, prodi_tab):
        return {"q": 12}  # 12/20 = 60% responden

    async def rating_feedback(self, kode, pertemuan_list, dosen, prodi_tab):
        # 6 responden rating 5, 6 responden rating 4 -> mean 4.5 (bukan 3.0)
        return [
            {"nim": str(i), "kode": kode, "pertemuan": str(p),
             "skorPemahaman": 5.0, "skorInteraktif": 5.0, "skorPerforma": 5.0, "csatGabungan": 5.0}
            for p in pertemuan_list for i in range(6)
        ] + [
            {"nim": f"9{i}", "kode": kode, "pertemuan": str(p),
             "skorPemahaman": 4.0, "skorInteraktif": 4.0, "skorPerforma": 4.0, "csatGabungan": 4.0}
            for p in pertemuan_list for i in range(6)
        ]


class _Ctx:
    def __init__(self, fake=None):
        self.bot_data = {"sheets": fake or _FakeSheets()}
        self.user_data = {"facilitator": "Fella Amalia S.T., M.T."}


class _Cls:
    code = "PPC01"
    subject = "Production Planning and Control"


async def _gather_skor():
    cls = sheets.ClassEntry(code="PPC01", subject="Production Planning and Control",
                            day="", time_range="", category="Reguler", lecturer="",
                            room="", rombel="", sks="", zoom_number="", zoom_link="")
    return await L._gather_report_data(_Ctx(), cls, [3, 4])


report = asyncio.run(_gather_skor())
m3, m4 = report["meetings"]
_check("skor P3 = mean rating 4.5 (bukan 5*0.6=3.0)", m3["skor"] == 4.5)
_check("skor P4 = mean rating 4.5", m4["skor"] == 4.5)
_check("skor != legacy 5*rate (3.0)", m3["skor"] != 3.0 and m4["skor"] != 3.0)
_check("kategori per meeting ada", m3["skorPerforma"] == 4.5 and m3["skorPemahaman"] == 4.5
       and m3["skorInteraktif"] == 4.5)
_check("responden tetap dari fb.q=12", m3["responden"] == 12)
cards = dict(report["cards"])
_check("card Gabungan mean csat 4.5", cards["CSAT Gabungan"] == 4.5)
_check("card Performa mean kategori 4.5", cards["Performa"] == 4.5)
_check("card Pemahaman mean kategori 4.5", cards["Pemahaman"] == 4.5)
_check("card Interaktivitas mean kategori 4.5", cards["Interaktivitas"] == 4.5)


# 10) responden BEDA per pertemuan: card harus MEAN SEDERHANA (web), bukan
#     tertimbang responden. P3 q=10 skor 4.0, P4 q=20 skor 5.0 ->
#     simple (4+5)/2 = 4.50; weighted (4*10+5*20)/30 = 4.67.
class _FakeSheetsUnequal:
    async def zoom_entries(self, name):
        return [
            {"kode": "PPC01", "pertemuan": "3", "tanggal": "15/09/2026",
             "dosen": "Fella Amalia S.T., M.T.", "subject": "Production Planning and Control"},
            {"kode": "PPC01", "pertemuan": "4", "tanggal": "17/09/2026",
             "dosen": "Fella Amalia S.T., M.T.", "subject": "Production Planning and Control"},
        ]

    async def find_rekap_tab(self, name):
        return ""

    async def rekap_rows(self, tab):
        return []

    async def absen_counts(self, kode, p):
        return {"total": 0, "hadir": 0, "feedback": 0, "prodi": ""}

    async def feedback_counts(self, kode, pertemuan, dosen, prodi_tab):
        return {"q": 10} if pertemuan == "3" else {"q": 20}

    async def rating_feedback(self, kode, pertemuan_list, dosen, prodi_tab):
        out = []
        for p in pertemuan_list:
            val = 4.0 if p == 3 else 5.0
            for i in range(10 if p == 3 else 20):
                out.append({"nim": f"{p}-{i}", "kode": kode, "pertemuan": str(p),
                            "skorPemahaman": val, "skorInteraktif": val,
                            "skorPerforma": val, "csatGabungan": val})
        return out


cls2 = sheets.ClassEntry(code="PPC01", subject="Production Planning and Control",
                         day="", time_range="", category="Reguler", lecturer="",
                         room="", rombel="", sks="", zoom_number="", zoom_link="")
rep2 = asyncio.run(L._gather_report_data(_Ctx(_FakeSheetsUnequal()), cls2, [3, 4]))
m3b, m4b = rep2["meetings"]
_check("responden tak sama: P3=10, P4=20",
       m3b["responden"] == 10 and m4b["responden"] == 20)
_check("skor P3 = 4.0, P4 = 5.0", m3b["skor"] == 4.0 and m4b["skor"] == 5.0)
cards2 = dict(rep2["cards"])
_check("card Gabungan simple mean 4.50 (bukan 4.67)", cards2["CSAT Gabungan"] == 4.50)
_check("card Gabungan TIDAK tertimbang (4.67)", cards2["CSAT Gabungan"] != 4.67)
_check("card Performa simple mean 4.50", cards2["Performa"] == 4.50)
_check("card Pemahaman simple mean 4.50", cards2["Pemahaman"] == 4.50)
_check("card Interaktivitas simple mean 4.50", cards2["Interaktivitas"] == 4.50)

print("ALL PASS")