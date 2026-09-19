"""repair_rekap_jam.py — one-time repair tool (sekali pakai, sudah disetujui).

Kolom D (Jam Perkuliahan) di tab rekap "Adzril Adzim" berisi START-ONLY
("18.00","20.00",...) padahal konvensi sheet = rentang penuh ("18.30 - 20.00").

Proses (sama persis dgn _resolve_jam_range di handlers/rekap.py):
  1. READ seluruh tab (kolom A..E).
  2. Kandidat = D cocok regex ^\\d{1,2}[.:]\\d{2}$ (start-only, TANPA " - ").
  3. Kode dr kolom E -> lookup kelas (personal+backup+makeup by nama
     "Adzril Adzim Hendrynov"), match normalize; tak ketemu -> SKIP & lapor.
     time_range penuh dari sheet master/backup/makeup dipakai.
  4. DRY-RUN cetak rencana (row, lama->baru). Dengan --write: tulis cuma sel D
     via update_rekap_cells (USER_ENTERED, guard grid, invalidate cache).
  5. VERIFY re-read setelah tulis -> before/after final.

SAFEGUARD: tab cuma dari _find_rekap_tab("Adzril Adzim Hendrynov"), cuma sel D,
cuma format start-only, max 10 sel.

Run:  py repair_rekap_jam.py            # dry-run
      py repair_rekap_jam.py --write    # tulis ke Google Sheets
      py repair_rekap_jam.py --selftest # cek lokal tanpa jaringan
"""
from __future__ import annotations

import argparse
import asyncio
import re
from functools import partial

from config import load_config
import sheets

FACILITATOR = "Adzril Adzim Hendrynov"
MAX_FIX = 10

_START_ONLY = re.compile(r"^\d{1,2}[.:]\d{2}$")


def _resolve_code(kode: str, classes: list[sheets.ClassEntry], fallback: str) -> str:
    """Reuse _resolve_jam_range lookup: match normalize; tak ketemu -> nilai asli
    (caller SKIP). Multiple matches dgn time_range SAMA dianggap deterministik."""
    kode = (kode or "").strip().casefold()
    hits = [c for c in classes if (c.code or "").strip().casefold() == kode]
    ranges = {c.time_range.strip() for c in hits if (c.time_range or "").strip()}
    if len(ranges) == 1:
        return next(iter(ranges))
    return (fallback or "").strip()


def _selftest() -> None:
    """Pure checks lokal tanpa jaringan."""
    assert _START_ONLY.match("18.00") and not _START_ONLY.match("18.00 - 20.00")
    assert _START_ONLY.match("9:30") and not _START_ONLY.match("")
    norm = lambda v: re.sub(r"\s+", " ", (v or "").strip()).casefold()
    assert norm("  IF-101  ") == "if-101"
    assert _resolve_code("NOPE-000", [], "18.00") == "18.00"  # tak ketemu -> SKIP
    entry = sheets.ClassEntry(code="IF-101", subject="X", day="Senin",
                              time_range="18.30 - 20.00", category="", lecturer="",
                              room="", rombel="", sks="", zoom_number="",
                              zoom_link="", keterangan="")
    assert _resolve_code("if-101", [entry], "18.00") == "18.30 - 20.00"
    dup1 = sheets.ClassEntry(code="IF-101", subject="X", day="Senin",
                             time_range="18.30 - 20.00", category="", lecturer="",
                             room="", rombel="", sks="", zoom_number="",
                             zoom_link="", keterangan="")
    dup2 = sheets.ClassEntry(code="IF-101", subject="X", day="Selasa",
                             time_range="18.30 - 20.00", category="Backup",
                             lecturer="", room="", rombel="", sks="",
                             zoom_number="", zoom_link="", keterangan="")
    assert _resolve_code("if-101", [dup1, dup2], "18.00") == "18.30 - 20.00"
    print("selftest OK")


async def main(apply: bool) -> None:
    cfg = load_config()
    c = sheets.SheetsClient(cfg)

    tab = await c.find_rekap_tab(FACILITATOR)
    print(f"tab rekap : {tab!r} (rekap_sheet_id={cfg.rekap_sheet_id})")

    # 1. READ seluruh tab, kolom A..E
    rows = await c._run(partial(c._cached_rows, cfg.rekap_sheet_id, tab))
    def cell(r, i):
        v = rows[r - 1][i].strip() if 0 < r <= len(rows) and len(rows[r - 1]) > i else ""
        return v
    print(f"\nDUMP baris 1..{len(rows)} (kolom A..E; D start-only ditandai *):")
    for r in range(1, len(rows) + 1):
        a, b, c_, d, e = (cell(r, i) for i in range(5))
        if not (a or b or c_ or d or e):
            continue
        mark = "*" if _START_ONLY.match(d) and e else " "
        print(f"  {mark}row {r}: A={a!r} B={b!r} C={c_!r} D={d!r} E={e!r}")

    # 2. kandidat
    candidates = []
    for r in range(1, len(rows) + 1):
        d = cell(r, 3)
        e = cell(r, 4)
        if _START_ONLY.match(d) and e:
            candidates.append((r, d, e))
        if len(candidates) > MAX_FIX:
            raise SystemExit(f"ABORT: >{MAX_FIX} kandidat ({len(candidates)}) — hentikan.")
    print(f"\nkandidat start-only: {len(candidates)} (max {MAX_FIX})")
    if not candidates:
        print("tidak ada kandidat. selesai.")
        return

    # 3. resolve time_range penuh dr jadwal master/backup/makeup
    personal, backup, makeup = await c.get_all_loggable_classes(FACILITATOR)
    classes = list(personal) + list(backup) + list(makeup)
    plan = []
    for r, old, kode in candidates:
        new = _resolve_code(kode, classes, old)
        if not new or new == old:
            print(f"  SKIP row {r} kode={kode!r} — tak ketemu/ambigu, dibiarkan {old!r}")
            continue
        plan.append((r, old, new, kode))

    # 4. print rencana
    print("\nRENCANA (DRY-RUN):")
    for r, old, new, kode in plan:
        print(f"  row {r} [{kode}]: {old!r} -> {new!r}")
    if not plan:
        print("tidak ada yang perlu diperbaiki. selesai.")
        return
    print(f"total {len(plan)} sel D akan ditulis.")
    if not apply:
        print("mode dry-run: tidak ada yang ditulis. pakai --write untuk tulis.")
        return

    # 5. WRITE hanya sel D
    print("\nWRITE per sel...")
    for r, old, new, kode in plan:
        await c.update_rekap_cells(tab, r, {"D": new})
        print(f"  wrote row {r} D: {old!r} -> {new!r}")

    # 6. VERIFY re-read (cache sudah invalidated oleh update_rekap_cells)
    rows2 = await c._run(partial(c._cached_rows, cfg.rekap_sheet_id, tab))
    print("\nVERIFY final (kolom D):")
    ok = 0
    for r, old, new, kode in plan:
        d2 = rows2[r - 1][3].strip() if 0 < r <= len(rows2) and len(rows2[r - 1]) > 3 else ""
        status = "OK" if d2 == new else "MISMATCH"
        if d2 == new:
            ok += 1
        print(f"  row {r} before={old!r} after={d2!r} [{status}]")
    print(f"\n{ok}/{len(plan)} sel terverifikasi. tab={tab!r}")
    if ok != len(plan):
        raise SystemExit("VERIFIKASI GAGAL — periksa manual.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="tulis ke Google Sheets (default dry-run)")
    ap.add_argument("--selftest", action="store_true", help="cek lokal tanpa jaringan")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
    else:
        asyncio.run(main(args.write))