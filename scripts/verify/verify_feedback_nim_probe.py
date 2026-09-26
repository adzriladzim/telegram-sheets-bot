"""Live probe (READ-ONLY): NIM format Feedback vs Absen + filter kolom.

Jawab untuk fitur sinkron SF/OF->S/O:
1. Format NIM di sheet Feedback (col 3 / index 2) vs sheet Absen (kolom NIM).
   leading-zero? full 26111600001 vs short 001?
2. Kolom kode/pertemuan/dosen yang dipakai filter Q (konsisten _feedback_counts):
   kode = blob r[8:25], pertemuan = r[26], dosen = r[25], school = r[6].
3. Untuk kode kelas yg ada di feedback: apakah NIM feedback cocok (exact /
   prefix / suffix / zero-pad) dgn NIM roster absen?

Run:  python verify_feedback_nim_probe.py
Exit: 0 ok, 2 skip.
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception as exc:  # pragma: no cover
    print(f"SKIP: deps missing: {exc}")
    sys.exit(2)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config import load_config  # noqa: E402

from sheets import SheetsClient  # noqa: E402  (reuse cache/filter helpers)

FEEDBACK_ID = "1dZQcq3TvPh7wkW0z8SF94YExs5jONYf_O3oV09Hk604"
FEEDBACK_TAB = "Form Responses 1"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def letter(idx: int) -> str:
    out = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


def rel(a: str, b: str) -> str:
    """Hubungan 2 NIM (a=abs, b=fb): exact / prefix / suffix / int-eq / lain."""
    if a == b:
        return "exact"
    if b and a.endswith(b):
        return "absen_suffix_fb (absen berakhiran fb)"
    if a and b.endswith(a):
        return "fb_suffix_absen (fb berakhiran absen)"
    if a.isdigit() and b.isdigit() and int(a) == int(b):
        return "int_eq"
    if a.isdigit() and b.isdigit() and len(b) < len(a) and a.endswith(b):
        return "absen_ends_with_short_fb"
    return "lain"


def main() -> int:
    cfg = load_config()
    base = Path(__file__).resolve().parents[2]
    sa_path = base / os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json").strip()
    if not sa_path.exists():
        print("SKIP: service account file not found")
        return 2

    s = SheetsClient(cfg)
    # Read-only: langsung panggil helper sync intern (tanpa tulis apa pun).
    frows = s._sheet_in(FEEDBACK_ID, FEEDBACK_TAB).get_all_values()
    print(f"FEEDBACK: {FEEDBACK_ID!r} :: {FEEDBACK_TAB!r}  rows={len(frows)}")

    # 1. header feedback
    hdr = frows[0] if frows else []
    print("\n== HEADER feedback (30 kolom) ==")
    for i, c in enumerate(hdr[:30]):
        print(f"  {letter(i)}: {c!r}")

    # 2. sample NIM feedback rows 2..15 (repr => tampak leading zero)
    print("\n== SAMPLE feedback NIM (row2..15) ==")
    for r in frows[1:15]:
        nim = r[2].strip() if len(r) > 2 else ""
        pm = r[26].strip() if len(r) > 26 else ""
        ds = r[25].strip() if len(r) > 25 else ""
        sc = r[6].strip() if len(r) > 6 else ""
        print(f"  row NIM={nim!r} pertemuan={pm!r} dosen={ds!r} school={sc!r}")

    # 3. kode2 real dari master
    mrows = s._cached_rows(cfg.sheet_id, cfg.master_sheet)
    kodes = sorted({r[4].strip() for r in mrows if len(r) > 4 and r[4].strip()})
    print(f"\nMaster kode count={len(kodes)} sample={kodes[:10]}")

    # 4. frekuensi kode di feedback (blob r[8:25])
    freq = Counter()
    for r in frows[1:]:
        if len(r) <= 26:
            continue
        blob = " ".join(c for c in r[8:25]).casefold()
        for k in kodes:
            if k.strip().casefold() in blob:
                freq[k] += 1
                break
    print("\nFeedback rows per kode (top 8):", freq.most_common(8))

    # 5. pilih 3 kode yg punya feedback
    chosen = [k for k, _ in freq.most_common(3) if k]
    if not chosen:
        print("NO kode match — pakai 2 kode pertama master")
        chosen = kodes[:2]

    print("\n== NIM RELATION (absen roster vs feedback) ==")
    for kode in chosen:
        try:
            blocks = s._locate_absen_block(kode)
        except Exception as exc:
            print(f"\n{kode}: absen block FAIL {exc}")
            continue
        absen_nims = set()
        titles = set()
        for title, rows, nim_header in blocks:
            titles.add(title)
            for r_idx in range(nim_header + 2, len(rows)):
                if rows[r_idx] and rows[r_idx][0].strip() == "Program Studi":
                    break
                if rows[r_idx] and rows[r_idx][0].strip():
                    absen_nims.add(rows[r_idx][0].strip())
        # feedback NIM utk kode ini (tanpa filter pertemuan/dosen) — gather raw
        fb_nims = []
        for r in frows[1:]:
            if len(r) <= 26:
                continue
            blob = " ".join(c for c in r[8:25]).casefold()
            if kode.strip().casefold() not in blob:
                continue
            nim = r[2].strip() if len(r) > 2 else ""
            if nim:
                fb_nims.append(nim)
        fb_unique = sorted(set(fb_nims))
        print(f"\n[{kode}] tabs={sorted(titles)}")
        print(f"  absen_nims={len(absen_nims)} sample={sorted(absen_nims)[:5]}")
        print(f"  fb_nims={len(fb_nims)} unique={len(fb_unique)} sample={fb_unique[:8]}")
        rels = Counter()
        for fb in fb_unique:
            hit = [a for a in absen_nims if rel(a, fb) != "lain"]
            for a in hit:
                rels[rel(a, fb)] += 1
        print(f"  relation matched counts: {dict(rels)}")
        # any fb_nim NOT matched?
        unmatched = [fb for fb in fb_unique
                     if not any(rel(a, fb) != "lain" for a in absen_nims)]
        print(f"  unmatched fb_nims: {unmatched[:10]} (total {len(unmatched)})")

    print("\nPROBE DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())