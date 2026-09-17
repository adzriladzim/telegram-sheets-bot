"""Live probe: header & existing rows tab Caylay Cancel & Pengganti.

READ-ONLY. Prints:
1. Exact column order of the cancel tab (row 1..3).
2. Existing data rows + whether col A ("No") is filled.
3. Proposed CancelRecord field -> column mapping vs current as_row order.
4. Any Faezal row: raw values + de-shift "correct per column" (no write).

Run:  python verify_cancel_header.py
Exit: 0 PASS, 1 FAIL, 2 skip (missing env/service account).
"""
from __future__ import annotations

import os
import re
import sys

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception as exc:  # pragma: no cover
    print(f"SKIP: deps missing: {exc}")
    sys.exit(2)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import load_config  # noqa: E402

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def letter(idx: int) -> str:
    """0-based -> 'A'..'Z','AA'.."""
    out = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


# Live header columns we care about (normalize like SheetsClient._normalize).
def norm(v: str) -> str:
    return " ".join((v or "").split()).casefold()


def main() -> int:
    cfg = load_config()

    # Same service-account resolution as config._service_account_path().
    from pathlib import Path
    base = Path(__file__).resolve().parent
    sa_path = base / os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json").strip()
    if not sa_path.exists():
        print("SKIP: service account file not found — set GOOGLE_SERVICE_ACCOUNT_JSON / secrets/")
        return 2

    gc = gspread.authorize(Credentials.from_service_account_file(str(sa_path), scopes=SCOPES))
    try:
        ws = gc.open_by_key(cfg.sheet_id).worksheet(cfg.cancel_sheet)
    except Exception as exc:
        print(f"FAIL: open tab failed: {exc}")
        return 1

    rows = ws.get_all_values()
    print(f"TAB: {cfg.cancel_sheet!r}  rows={len(rows)}")

    # ---- 1. headers ----
    print("\n== HEADER ROWS (raw, index -> col letter) ==")
    for r_idx in range(min(3, len(rows))):
        cells = [(letter(i), v) for i, v in enumerate(rows[r_idx])]
        print(f"row{r_idx+1}: {cells}")

    # ---- 2. header mapping for CancelRecord fields ----
    # Find the header row that mentions 'nama dosen' (usually row 1).
    hdr_idx = None
    for i, r in enumerate(rows[:5]):
        if any("dosen" in norm(c) for c in r):
            hdr_idx = i
            break
    if hdr_idx is None:
        print("\nFAIL: header row with 'Dosen' not found in first 5 rows")
        return 1
    hdr = rows[hdr_idx]

    wanted = {
        "no": ["no"],
        "dosen": ["nama dosen", "dosen"],
        "matkul": ["nama mata kuliah", "mata kuliah", "matkul"],
        "kode": ["kode kelas", "kode"],
        "sesi": ["sesi", "pertemuan"],
        "jadwal": ["jadwal awal", "jadwal"],
        "jam": ["jam"],
        "sks": ["sks"],
        "fasil": ["fasilitator", "fasil"],
    }
    mapping = {}
    for key, needles in wanted.items():
        hits = []
        for i, c in enumerate(hdr):
            cn = norm(c)
            if not cn:
                continue
            if any(n in cn for n in needles):
                hits.append((i, c))  # 0-based index, raw header text
        mapping[key] = hits
        print(f"  {key:8s} -> {hits}")

    if not mapping["kode"] or not mapping["sesi"] or not mapping["jadwal"]:
        print("\nFAIL: header missing Kode Kelas / Sesi / Jadwal Awal")
        return 1

    def col_of(key: str) -> int:
        return mapping[key][0][0]  # 0-based col index, first hit

    print("\n== RESOLVED (first hit per field) ==")
    resolved = {k: col_of(k) for k in wanted}
    for k, idx in resolved.items():
        print(f"  {k:8s} -> col {letter(idx)}  header={hdr[idx].strip()!r}")

    # ---- 3. existing rows + col A "No" ----
    print("\n== DATA ROWS (first 6 with any content) ==")
    data_rows = []
    for i, r in enumerate(rows[hdr_idx + 1:], start=hdr_idx + 1):
        if any((c or "").strip() for c in r):
            data_rows.append((i, r))
        if len(data_rows) >= 6:
            break
    for i, r in data_rows:
        print(f"row{i+1}: A={r[0]!r} " + ", ".join(f"{letter(j)}={v!r}" for j, v in enumerate(r[:10] if len(r) > 10 else r)))

    a_filled = any((r[0] or "").strip().isdigit() for _, r in data_rows)
    print(f"\nCol A 'No' filled with numbers in existing rows? -> {a_filled}")

    # ---- 4. stub mapping: sheets.CancelRecord.as_row() vs live header ----
    print("\n== STUB MAPPING (CancelRecord -> live header) ==")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import sheets  # noqa: E402
    rec = sheets.CancelRecord(
        lecturer="<DOSEN>", subject="<MATKUL>", jadwal_awal="<JADWAL>", jam="<JAM>",
        sesi="<SESI>", kode="<KODE>", sks="<SKS>", facilitator="<FASIL>")
    row = rec.as_row()
    sentinel_to_field = {
        "<DOSEN>": "dosen", "<MATKUL>": "matkul", "<KODE>": "kode", "<SESI>": "sesi",
        "<JADWAL>": "jadwal", "<JAM>": "jam", "<SKS>": "sks", "<FASIL>": "fasil",
    }
    bad = []
    for pos, val in enumerate(row):  # pos 0 -> col B (idx 1)
        col_idx = pos + 1
        fld = sentinel_to_field.get(val)
        need_idx = resolved[fld] if fld else None
        ok = fld and need_idx == col_idx
        print(f"  col {letter(col_idx)} field={fld!r} need={letter(need_idx) if need_idx is not None else '?'} -> {'OK' if ok else 'MISMATCH'}")
        if not ok:
            bad.append((letter(col_idx), fld, letter(need_idx) if need_idx is not None else "?"))
    if bad:
        print(f"\nFAIL: as_row misaligned at {bad}")
        return 1
    print("  as_row selaras dengan header live.")

    # ---- 5. Faezal row: show raw + de-shift (no write) ----
    print("\n== FAEZAL ROWS (display only, NO WRITE) ==")
    found = False

    def looks_like_kode(v: str) -> bool:
        # live codes: "Pred1", "Deep1", "BsDg2", "AiDP2" — letters+digits, no spaces/dates
        s = v.strip()
        return bool(s) and len(s) <= 12 and re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,10}", s) is not None

    def looks_like_date(v: str) -> bool:
        s = v.strip().casefold()
        return "/" in s or any(m in s for m in ("januari", "februari", "maret", "april", "mei", "juni", "juli", "agustus", "september", "oktober", "november", "desember"))

    for i, r in enumerate(rows[hdr_idx + 1:], start=hdr_idx + 1):
        joined = " ".join(r)
        if "faezal" not in joined.casefold():
            continue
        found = True
        d_cell = r[resolved["kode"]] if len(r) > resolved["kode"] else ""
        broken = not looks_like_kode(d_cell) or looks_like_date(d_cell)
        print(f"row{i+1} {'[BROKEN old-order]' if broken else '[OK live-order] '} RAW: "
              + ", ".join(f"{letter(j)}={v!r}" for j, v in enumerate(r[:10] if len(r) > 10 else r)))
        if not broken:
            print("     row sudah sesuai header live — tidak perlu koreksi.")
            continue
        # De-shift assuming row was written by OLD as_row (B,C,D,E,F,G,H,I =
        # dosen,matkul,jadwal,jam,sesi,kode,sks,fasil) into NEW header.
        old = {
            "B": r[1] if len(r) > 1 else "",
            "C": r[2] if len(r) > 2 else "",
            "D": r[3] if len(r) > 3 else "",  # old jadwal_awal
            "E": r[4] if len(r) > 4 else "",  # old jam
            "F": r[5] if len(r) > 5 else "",  # old sesi
            "G": r[6] if len(r) > 6 else "",  # old kode
            "H": r[7] if len(r) > 7 else "",  # old sks
            "I": r[8] if len(r) > 8 else "",  # old fasil
        }
        print("     Koreksi manual — nilai benar per kolom header live:")
        for k in ["dosen", "matkul", "kode", "sesi", "jadwal", "jam", "sks", "fasil"]:
            cl = letter(resolved[k])
            src = {"dosen": old["B"], "matkul": old["C"], "kode": old["G"], "sesi": old["F"],
                   "jadwal": old["D"], "jam": old["E"], "sks": old["H"], "fasil": old["I"]}[k]
            print(f"       {cl} ({k:6s}) = {src!r}")
    if not found:
        print("  no Faezal row found in cancel tab")

    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())