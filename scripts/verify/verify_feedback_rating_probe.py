"""Live probe (READ-ONLY): header Form Responses 1 + map kolom rating CSAT.

Jawab utk /laporan skor fix:
1. Dump 40 kolom header Form Responses 1 (indeks + nama persis).
2. Temukan kolom rating via header-match web CSAT:
   pemahaman -> skorPemahaman, interaktif -> skorInteraktif,
   performa/kepuasan -> skorPerforma.
   Heuristic: cari kata kunci di header (casefold): 'pemahaman', 'interakt',
   'performa'/'kepuasan', plus 'skor'/'rating'/'penilaian' dsb.
3. Sample nilai rating baris 2..6 di kolom hasil map + kolom 10..24 mentah.

Run:  python verify_feedback_rating_probe.py
Exit: 0 ok, 2 skip.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception as exc:  # pragma: no cover
    print(f"SKIP: deps missing: {exc}")
    sys.exit(2)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config import load_config  # noqa: E402

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


def web_index(hdr: str) -> str | None:
    """Header-match web CSAT. Return 'performa'|'pemahaman'|'interaktif'|None."""
    h = (hdr or "").casefold()
    if "pemahaman" in h:
        return "pemahaman"
    if "interakt" in h:
        return "interaktif"
    if "performa" in h or "kepuasan" in h:
        return "performa"
    return None


def main() -> int:
    cfg = load_config()
    base = Path(__file__).resolve().parents[2]
    sa_path = base / os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json").strip()
    if not sa_path.exists():
        print("SKIP: service account file not found")
        return 2

    creds = Credentials.from_service_account_file(str(sa_path), scopes=SCOPES)
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(FEEDBACK_ID)
    ws = ss.worksheet(FEEDBACK_TAB)
    rows = ws.get_all_values()
    print(f"FEEDBACK: {FEEDBACK_ID!r} :: {FEEDBACK_TAB!r}  rows={len(rows)}")

    hdr = rows[0] if rows else []
    print(f"\n== HEADER (0..40) ==")
    for i, c in enumerate(hdr[:40]):
        tag = web_index(c)
        mark = f"  <-- {tag}" if tag else ""
        print(f"  {letter(i)} [{i}]: {c!r}{mark}")

    # map
    mapped = {}
    for i, c in enumerate(hdr):
        tag = web_index(c)
        if tag and tag not in mapped:
            mapped[tag] = i
    print(f"\nMAPPED (first match): {mapped}")
    keys = web_index(hdr[0]) or ""
    if not mapped:
        print("NO rating columns found by web header-match — dump raw cols 9..30 sample")
        for r in rows[1:6]:
            print(" ", [r[i] if len(r) > i else "" for i in range(9, 30)])
        print("\nPROBE DONE (no map)")
        return 0

    print("\n== SAMPLE rating values (row 2..6) ==")
    for idx, r in enumerate(rows[1:6], start=2):
        cells = {k: (r[i].strip() if len(r) > i else "") for k, i in mapped.items()}
        print(f"  row{idx}: {cells}")

    # distribution skim: numeric? which rows non-empty
    n = len(rows) - 1
    nonempty = {k: 0 for k in mapped}
    numeric = {k: 0 for k in mapped}
    for r in rows[1:]:
        for k, i in mapped.items():
            if len(r) > i and r[i].strip():
                nonempty[k] += 1
                try:
                    float(r[i].replace(",", "."))
                    numeric[k] += 1
                except ValueError:
                    pass
    print("\n== coverage rating ==")
    for k in mapped:
        print(f"  {k}: col {letter(mapped[k])} [{mapped[k]}] nonempty={nonempty[k]}/{n} numeric={numeric[k]}")

    print("\nPROBE DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())