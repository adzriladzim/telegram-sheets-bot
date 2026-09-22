"""repair_rekap_sks_gabungan.py — one-time repair tool (sekali pakai).

Baris rekap LAMA (dicatat sebelum fix `_build_base` sks×len) menulis kolom H
(SKS) = sks DASAR walau pertemuan G gabungan ("3 dan 4" = 2 sesi). Script ini
perbaiki baris yang JELAS-single saja:

  jelas  : H (SKS row) == sks dasar Zoom Record J — SATU-SATUNYA nilai utk kode
           DAN G (pertemuan) punya >=2 angka.
  → H = int(H) * len(nums)
  ambigu : zoom tak ketemu / H != J / G cuma 1 angka / H bukan digit
           → JANGAN DISENTUH (skip & lapor).

Mode:
  py repair_rekap_sks_gabungan.py              # dry-run tab FACILITATOR (single-tab)
  py repair_rekap_sks_gabungan.py --all-tabs   # SEMUA tab rekap (skip _SYSTEM_TABS)
  py repair_rekap_sks_gabungan.py --write      # tulis ke Google Sheets (default dry-run)
  py repair_rekap_sks_gabungan.py --selftest   # cek lokal tanpa jaringan

SAFEGUARD: cuma kolom H, hanya jelas-single, skip _SYSTEM_TABS, cap global 200 sel, tab rekap = hanya dari rekap_sheet_id.
Reuse plumbing repair jam: _retry, _collect_tabs, _commit_hash.
"""
from __future__ import annotations

import argparse
import asyncio
import re
from functools import partial

import sheets
import users
from config import BASE_DIR, load_config
from repair_rekap_jam import _collect_tabs, _commit_hash, _retry

MAX_ALL = 200

_NUM = re.compile(r"\d+")


def _zoom_sks_map(zoom_rows: list[list[str]]) -> dict[str, set[str]]:
    """kode.casefold -> {sks} dari SEMUA baris Zoom Record (F=kode, J=sks).
    Satu kode boleh punya 1 nilai sks (kelas sama, sks sama)."""
    m: dict[str, set[str]] = {}
    for i, r in enumerate(zoom_rows):
        if i == 0 or len(r) <= 9:
            continue
        kode = r[5].strip()
        sks = r[9].strip()
        if kode and sks and re.fullmatch(r"\d+", sks):
            m.setdefault(kode.casefold(), set()).add(sks)
    return m


def _collect_candidates(rows: list[list[str]]) -> list[tuple[int, str, str, str]]:
    """(row, pertemuan_G, sks_H, kode_E) — gabungan (>=2 angka) DAN H digit murni.
    Butuh B(tanggal) + E(kode) ada (bukan baris kosong/separator).
    Pengecualian: kode CDC* / AsDs* (case-insensitive) TAK jadi kandidat —
    ikut master apa adanya, jangan disentuh."""
    out = []
    for i, r in enumerate(rows):
        if len(r) <= 7:
            continue
        tgl = r[1].strip()
        kode = r[4].strip()
        prtm = r[6].strip()
        sks = r[7].strip()
        if not (tgl and kode):
            continue
        if (kode or "").casefold().startswith(("cdc", "asds")):
            continue
        if len(_NUM.findall(prtm)) >= 2 and re.fullmatch(r"\d+", sks):
            out.append((i + 1, prtm, sks, kode))
    return out


def _fix_sks(prtm: str, sks: str, kode: str, sks_map: dict[str, set[str]]) -> str | None:
    """H baru utk jelas-single, None = ambigu/jangan sentuh.
    Clear: H == satu-satunya zoom J utk kode -> int(H) * len(nums).
    CDC* / AsDs* (case-insensitive) selalu None — master apa adanya."""
    if (kode or "").strip().casefold().startswith(("cdc", "asds")):
        return None
    nums = _NUM.findall(prtm or "")
    if len(nums) < 2 or not re.fullmatch(r"\d+", (sks or "").strip()):
        return None
    base = sks_map.get((kode or "").strip().casefold())
    if not base or len(base) != 1:
        return None
    if next(iter(base)) != (sks or "").strip():
        return None
    return str(int((sks or "").strip()) * len(nums))


def _selftest() -> None:
    """Pure checks lokal tanpa jaringan."""
    assert _NUM.findall("3 dan 4") == ["3", "4"] and _NUM.findall("4") == ["4"]
    rows = [
        ["1", "6 September 2026", "Dosen", "18.00", "IF-101", "Matkul", "3 dan 4", "2"],  # jelas
        ["2", "6 September 2026", "Dosen", "18.00", "IF-102", "Matkul", "3", "2"],        # single -> no
        ["3", "", "Dosen", "18.00", "IF-103", "Matkul", "3 dan 4", "2"],                  # tanpa tanggal -> no
        ["4", "6 September 2026", "Dosen", "18.00", "IF-104", "Matkul", "3 dan 4", "4"], # H!=zoom J -> no
        ["5", "6 September 2026", "Dosen", "18.00", "IF-105", "Matkul", "3 dan 4", "2"], # zoom tak ketemu -> no
        ["6", "6 September 2026", "Dosen", "18.00", "CDC123", "Matkul", "3 dan 4", "2"], # CDC -> no (master apa adanya)
        ["7", "6 September 2026", "Dosen", "18.00", "AsDs2", "Matkul", "1 dan 2", "4"],  # AsDs -> no (master apa adanya)
    ]
    cand = _collect_candidates(rows)
    # kandidat = semua gabungan+digit (1,4,5); row 2 single & row 3 tanpa tanggal
    # & row 6-7 CDC/AsDs bukan kandidat.
    # Ambigu (4: H!=zoom J, 5: zoom tak ketemu) baru digugurkan di _fix_sks -> SKIP.
    assert [c[0] for c in cand] == [1, 4, 5], cand
    m = {"if-101": {"2"}, "if-104": {"2"}}
    assert _fix_sks("3 dan 4", "2", "IF-101", m) == "4"
    assert _fix_sks("3 dan 4", "4", "IF-104", m) is None   # H!=zoom J -> ambigu
    assert _fix_sks("3 dan 4", "2", "IF-105", m) is None    # tak ketemu -> ambigu
    assert _fix_sks("3", "2", "IF-101", m) is None          # single -> no
    assert _fix_sks("3 dan 4", "dua", "IF-101", m) is None  # bukan digit -> no
    assert _fix_sks("3 dan 4", "3", "IF-101", m) is None    # H!=zoom J -> no
    assert _fix_sks("3 dan 4", "2", "CDC123", m) is None    # CDC -> no (master)
    assert _fix_sks("1 dan 2", "4", "AsDs2", m) is None     # AsDs -> no (master)
    assert _fix_sks("1 dan 2", "4", "asds2", m) is None     # case-insensitive
    zr = [["Hdr"] * 12, ["1", "01/09/2026", "Ratu", "06/09/2026", "1", "ARCH1", "Arsitektur", "3 dan 4", "Offline", "2", "Pro", "Budi"]]
    zm = _zoom_sks_map(zr)
    assert zm == {"arch1": {"2"}}, zm
    assert len(_commit_hash([("T", 1, "2", "4")])) == 64
    print("selftest OK")


async def main(apply: bool, all_tabs: bool) -> None:
    cfg = load_config()
    users.init(BASE_DIR / "data" / "users.json", default_name=cfg.facilitator_name)
    c = sheets.SheetsClient(cfg)

    tabs = await _collect_tabs(c, cfg, all_tabs)
    zoom_rows = await _retry(lambda: c._run(partial(c._cached_rows, cfg.sheet_id, cfg.zoom_record_sheet)))
    sks_map = _zoom_sks_map(zoom_rows)
    print(f"tab rekap : {len(tabs)} tab (rekap_sheet_id={cfg.rekap_sheet_id})")

    plan_all: list[tuple[str, int, str, str, str]] = []
    skips_all: list[tuple[str, int, str, str]] = []

    for tab, name in tabs:
        rows = await _retry(lambda: c._run(partial(c._cached_rows, cfg.rekap_sheet_id, tab)))
        cand = _collect_candidates(rows)
        if len(plan_all) + len(cand) > MAX_ALL:
            raise SystemExit(f"ABORT: total kandidat >{MAX_ALL} — hentikan.")
        if not cand:
            print(f"\n=== TAB {tab!r}: tidak ada kandidat gabungan.")
            continue
        plans, skips = [], []
        for row, prtm, sks, kode in cand:
            new = _fix_sks(prtm, sks, kode, sks_map)
            if new is None:
                skips.append((tab, row, kode, sks))
            else:
                plans.append((tab, row, sks, new, kode))
        plan_all += plans
        skips_all += skips
        print(f"\n=== TAB {tab!r} ({name or 'fallback'}: kandidat {len(cand)}, plan {len(plans)} ===")
        for p in plans:
            print(f"  row {p[1]} [{p[4]}] H: {p[2]!r} -> {p[3]!r}")
        for s in skips:
            print(f"  SKIP row {s[1]} kode={s[2]!r} H={s[3]!r} — ambigu, dibiarkan")

    print(f"\nTOTAL rencana: {len(plan_all)} sel H ({len(tabs)} tab)")
    if skips_all:
        print("DAFTAR SKIP:")
        for tab, row, kode, sks in skips_all:
            print(f"  {tab!r} row {row} kode={kode!r} — dibiarkan H={sks!r}")
    if not plan_all:
        print("tidak ada yang perlu diperbaiki. selesai.")
        return
    if not apply:
        print("mode dry-run: tidak ada yang ditulis. pakai --write untuk tulis.")
        return

    print("\nWRITE per sel...")
    for tab, row, old, new, kode in plan_all:
        await _retry(lambda: c.update_rekap_cells(tab, row, {"H": new}))
        print(f"  wrote {tab!r} row {row} H: {old!r} -> {new!r}")

    print("\nVERIFY final:")
    ok = 0
    for tab, row, old, new, kode in plan_all:
        rows2 = await _retry(lambda: c._run(partial(c._cached_rows, cfg.rekap_sheet_id, tab)))
        h2 = rows2[row - 1][7].strip() if 0 < row <= len(rows2) and len(rows2[row - 1]) > 7 else ""
        status = "OK" if h2 == new else "MISMATCH"
        if h2 == new:
            ok += 1
        print(f"  {tab!r} row {row} before={old!r} after={h2!r} [{status}]")
    commit = _commit_hash([(tab, row, old, new) for tab, row, old, new, _ in plan_all])
    print(f"\n{ok}/{len(plan_all)} sel terverifikasi.")
    print(f"commit sha256: {commit}")
    if ok != len(plan_all):
        raise SystemExit("VERIFIKASI GAGAL — periksa manual.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="tulis ke Google Sheets (default dry-run)")
    ap.add_argument("--all-tabs", action="store_true",
                    help="sapu SEMUA tab rekap (skip _SYSTEM_TABS). Cap 200.")
    ap.add_argument("--selftest", action="store_true", help="cek lokal tanpa jaringan")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
    else:
        asyncio.run(main(args.write, args.all_tabs))