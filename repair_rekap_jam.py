"""repair_rekap_jam.py — one-time repair tool (sekali pakai, sudah disetujui).

Kolom D (Jam Perkuliahan) di tab rekap berisi START-ONLY ("18.00","20.00",...)
padahal konvensi sheet = rentang penuh ("18.30 - 20.00").

Mode:
  py repair_rekap_jam.py              # dry-run tab FACILITATOR (single-tab)
  py repair_rekap_jam.py --all-tabs   # SEMUA tab rekap (skip _SYSTEM_TABS)
  py repair_rekap_jam.py --write      # tulis ke Google Sheets (default dry-run)
  py repair_rekap_jam.py --selftest   # cek lokal tanpa jaringan

--all-tabs: tab->nama via users registry (data/users.json) + find_rekap_tab;
nama tak diketemukan -> fallback resolve by KODE scan master sheet
(time_range penuh; unik -> pakai, ambigu -> SKIP & lapor).

Kandidat: D cocok regex ^\\d{1,2}[.:]\\d{2}$ (start-only) DAN baris punya
B(tanggal) + E(kode). Cap global 200 sel.

Proses:
  1. READ seluruh tab, kolom A..E.
  2. resolve time_range penuh: kelas by nama (personal+backup+makeup) atau
     KODE scan master (fallback). Tak ketemu/ambigu -> SKIP & lapor.
  3. DRY-RUN cetak rencana (row, lama->baru) per tab + daftar SKIP.
  4. Dengan --write: tulis cuma sel D via update_rekap_cells
     (USER_ENTERED, guard grid, invalidate cache).
  5. VERIFY re-read setelah tulis -> before/after final + hash commit.

SAFEGUARD: cuma kolom D, cuma format start-only, skip _SYSTEM_TABS,
cap global 200 sel, tab rekap = cuma dari rekap_sheet_id.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
from functools import partial
from typing import Any

import sheets
import users
from config import BASE_DIR, load_config

FACILITATOR = "Adzril Adzim Hendrynov"
MAX_FIX = 10    # mode single-tab legacy
MAX_ALL = 200   # cap global --all-tabs

_START_ONLY = re.compile(r"^\d{1,2}[.:]\d{2}$")

_RETRY_WAIT = 65   # detik tunggu saat 429 (quota read per menit per user)


async def _retry(fn, attempts: int = 6) -> Any:
    """Jalankan fn() async; transient error (429 rate-limit / koneksi putus)
    -> tunggu ~1 menit lalu ulang. Error nyata (tab tak ditemukan, guard
    grid, dsb.) langsung raise."""
    for i in range(attempts):
        try:
            return await fn()
        except sheets.SheetsError as exc:
            msg = str(exc)
            transient = any(k in msg for k in (
                "429", "jaringan", "kredensial", "Quota",
                "RemoteDisconnected", "Connection aborted", "timed out",
            ))
            if not transient or i == attempts - 1:
                raise
            print(f"  transient ({msg[:60]}...) — tunggu {_RETRY_WAIT}s (percobaan {i + 2}/{attempts})...")
            await asyncio.sleep(_RETRY_WAIT)
    raise RuntimeError("unreachable")


def _resolve_code(kode: str, classes: list[sheets.ClassEntry], fallback: str) -> str:
    """Reuse _resolve_jam_range lookup: match normalize; tak ketemu -> nilai asli
    (caller SKIP). Multiple matches dgn time_range SAMA dianggap deterministik."""
    kode = (kode or "").strip().casefold()
    hits = [c for c in classes if (c.code or "").strip().casefold() == kode]
    ranges = {c.time_range.strip() for c in hits if (c.time_range or "").strip()}
    if len(ranges) == 1:
        return next(iter(ranges))
    return (fallback or "").strip()


def _resolve_kode_master(kode: str, master_map: dict[str, set[str]], fallback: str) -> str:
    """Fallback no-nama: KODE scan master sheet. Unik -> pakai; ambigu -> SKIP."""
    s = (kode or "").strip().casefold()
    ranges = master_map.get(s) or set()
    if len(ranges) == 1:
        return next(iter(ranges))
    return (fallback or "").strip()


def _collect_candidates(rows: list[list[str]]) -> list[tuple[int, str, str, str]]:
    """(row, old_d, kode, tanggal) — D start-only DAN B(tanggal)+E(kode) ada."""
    out = []
    for i, r in enumerate(rows):
        row = i + 1
        d = r[3].strip() if len(r) > 3 else ""
        kode = r[4].strip() if len(r) > 4 else ""
        tgl = r[1].strip() if len(r) > 1 else ""
        if _START_ONLY.match(d) and kode and tgl:
            out.append((row, d, kode, tgl))
    return out


async def _master_kode_map(c: sheets.SheetsClient, cfg) -> dict[str, set[str]]:
    """kode.casefold -> {time_range} dari master sheet (fallback no-nama)."""
    rows = await _retry(lambda: c.sheet_rows(cfg.master_sheet))
    m: dict[str, set[str]] = {}
    for i, r in enumerate(rows):
        if i == 0 or len(r) <= 5:
            continue
        kode = r[4].strip().casefold()
        jam = r[3].strip()
        if kode and jam:
            m.setdefault(kode, set()).add(jam)
    return m


async def _collect_tabs(c: sheets.SheetsClient, cfg, all_tabs: bool) -> list[tuple[str, str | None]]:
    """[(tab, nama|None)] — single-tab: FACILITATOR. all_tabs: semua tab rekap
    kecuali _SYSTEM_TABS; nama dari users registry via find_rekap_tab.
    Tab dipakai EXACT raw title (worksheet() butuh exact, mis. 'Anisa ')."""
    if not all_tabs:
        return [(await _retry(lambda: c.find_rekap_tab(FACILITATOR)), FACILITATOR)]
    skip = {c._normalize(t) for t in sheets._SYSTEM_TABS}
    raw_tabs = await _retry(lambda: c._run(partial(c._tabs, cfg.rekap_sheet_id)))
    raw_by_norm: dict[str, str] = {}
    for t in raw_tabs:
        tn = c._normalize(t)
        if tn and tn not in raw_by_norm:
            raw_by_norm[tn] = t
    # tab -> nama terbaik (normalize terpanjang) dari registry + legacy default.
    tab_names: dict[str, str] = {}
    for name in set(users.registry().all().values()) | {cfg.facilitator_name}:
        name = (name or "").strip()
        if not name:
            continue
        try:
            t = await _retry(lambda: c.find_rekap_tab(name))
        except sheets.SheetsError:
            continue
        assert isinstance(t, str)
        tn = c._normalize(t)
        raw = raw_by_norm[tn] if tn in raw_by_norm else t
        cur = tab_names.get(raw)
        if cur is None or len(c._normalize(name)) > len(c._normalize(cur)):
            tab_names[raw] = name
    out = []
    for t in raw_tabs:
        if not t.strip() or c._normalize(t) in skip:
            continue
        out.append((t, tab_names.get(t)))
    return out


def _commit_hash(written: list[tuple[str, int, str, str]]) -> str:
    payload = "\n".join(f"{tab}\t{row}\t{old}\t{new}" for tab, row, old, new in written)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _selftest() -> None:
    """Pure checks lokal tanpa jaringan."""
    assert _START_ONLY.match("18.00") and not _START_ONLY.match("18.00 - 20.00")
    assert _START_ONLY.match("9:30") and not _START_ONLY.match("")
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
    # fallback master: unik -> pakai; ambigu -> SKIP
    assert _resolve_kode_master("if-101", {"if-101": {"18.30 - 20.00"}}, "18.00") == "18.30 - 20.00"
    assert _resolve_kode_master("if-102", {"if-102": {"18.30 - 20.00", "13.00 - 15.30"}}, "18.00") == "18.00"
    # kandidat butuh B(tanggal)+E(kode); tanpa B -> bukan kandidat
    rows = [
        ["1", "6 September 2026", "Dosen", "18.00", "IF-101"],
        ["2", "6 September 2026", "Dosen", "18.00 - 20.00", "IF-101"],  # bukan start-only
        ["3", "", "Dosen", "18.00", "IF-102"],                          # tanpa tanggal
        ["4", "7 September 2026", "Dosen", "18.00", "IF-102"],          # kandidat
    ]
    cand = _collect_candidates(rows)
    assert [c[0] for c in cand] == [1, 4]
    assert len(_commit_hash([("T", 1, "a", "b")])) == 64
    print("selftest OK")


async def main(apply: bool, all_tabs: bool) -> None:
    cfg = load_config()
    users.init(BASE_DIR / "data" / "users.json", default_name=cfg.facilitator_name)
    c = sheets.SheetsClient(cfg)

    tabs = await _collect_tabs(c, cfg, all_tabs)
    master_map = await _master_kode_map(c, cfg) if all_tabs else {}
    print(f"tab rekap : {len(tabs)} tab (rekap_sheet_id={cfg.rekap_sheet_id})")
    for t, name in tabs:
        print(f"  {t!r}  <- nama {name!r}" if name else f"  {t!r}  (fallback: KODE scan master)")

    plan_all: list[tuple[str, int, str, str, str]] = []
    skips_all: list[tuple[str, int, str, str]] = []

    # 1+2: per tab READ -> kandidat (cap global) -> resolve
    for tab, name in tabs:
        rows = await _retry(lambda: c._run(partial(c._cached_rows, cfg.rekap_sheet_id, tab)))
        cand = _collect_candidates(rows)
        if len(plan_all) + len(cand) > MAX_ALL:
            raise SystemExit(f"ABORT: total kandidat >{MAX_ALL} — hentikan.")
        if not cand:
            print(f"\n=== TAB {tab!r}: tidak ada kandidat start-only.")
            continue
        # resolve: by nama -> (personal+backup+makeup); no-nama -> KODE master
        if name:
            personal, backup, makeup = await _retry(partial(c.get_all_loggable_classes, name))
            classes = list(personal) + list(backup) + list(makeup)
        else:
            classes = []
        plans, skips = [], []
        for row, old, kode, tgl in cand:
            new = (_resolve_code(kode, classes, old) if classes
                   else _resolve_kode_master(kode, master_map, old))
            if not new or new == old:
                skips.append((tab, row, kode, old))
            else:
                plans.append((tab, row, old, new, kode))
        plan_all += plans
        skips_all += skips
        # 3. rencana per tab
        print(f"\n=== TAB {tab!r} ({name or 'fallback master'}): kandidat {len(cand)}, plan {len(plans)} ===")
        for p in plans:
            print(f"  row {p[1]} [{p[4]}]: {p[2]!r} -> {p[3]!r}")
        for s in skips:
            print(f"  SKIP row {s[1]} kode={s[2]!r} — tak ketemu/ambigu, dibiarkan {s[3]!r}")

    # ringkasan + daftar SKIP
    print(f"\nTOTAL rencana: {len(plan_all)} sel D ({len(tabs)} tab)")
    if skips_all:
        print("DAFTAR SKIP:")
        for tab, row, kode, old in skips_all:
            print(f"  {tab!r} row {row} kode={kode!r} — dibiarkan {old!r}")
    if not plan_all:
        print("tidak ada yang perlu diperbaiki. selesai.")
        return
    if not apply:
        print("mode dry-run: tidak ada yang ditulis. pakai --write untuk tulis.")
        return

    # 4. WRITE hanya sel D (per tab, per sel)
    print("\nWRITE per sel...")
    for tab, row, old, new, kode in plan_all:
        await _retry(lambda: c.update_rekap_cells(tab, row, {"D": new}))
        print(f"  wrote {tab!r} row {row} D: {old!r} -> {new!r}")

    # 5. VERIFY re-read -> before/after final
    print("\nVERIFY final:")
    ok = 0
    for tab, row, old, new, kode in plan_all:
        rows2 = await _retry(lambda: c._run(partial(c._cached_rows, cfg.rekap_sheet_id, tab)))
        d2 = rows2[row - 1][3].strip() if 0 < row <= len(rows2) and len(rows2[row - 1]) > 3 else ""
        status = "OK" if d2 == new else "MISMATCH"
        if d2 == new:
            ok += 1
        print(f"  {tab!r} row {row} before={old!r} after={d2!r} [{status}]")
    commit = _commit_hash([(tab, row, old, new) for tab, row, old, new, _ in plan_all])
    print(f"\n{ok}/{len(plan_all)} sel terverifikasi.")
    print(f"commit sha256: {commit}")
    if ok != len(plan_all):
        raise SystemExit("VERIFIKASI GAGAL — periksa manual.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="tulis ke Google Sheets (default dry-run)")
    ap.add_argument("--all-tabs", action="store_true",
                    help="sapu SEMUA tab rekap (skip _SYSTEM_TABS), nama via registry;"
                         " no-nama -> fallback KODE scan master. Cap 200.")
    ap.add_argument("--selftest", action="store_true", help="cek lokal tanpa jaringan")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
    else:
        asyncio.run(main(args.write, args.all_tabs))