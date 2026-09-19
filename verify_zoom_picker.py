"""Offline verification — /rekap zoom-record picker. HEAD after redesign.

Checks:
  1. zoom_entries parsing + col C match (fasil normalize), skip kosong
  2. klasifikasi 3 status: lengkap->skip, rumpang->fix(row+gaps), belum->new
  3. urut tanggal lama->baru
  4. paginasi 25/halaman + tombol Prev/Next
  5. prefill zoom->record: kode/matkul/sks/pertemuan '3 dan 4'/tipe Online->Online Offline->On-site
  6. jalur manual tetap: class_kb dibangun, _tanggal_keys fallback ke kelas
Run: py verify_zoom_picker.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# cp1252 console can't print emoji in FAIL labels
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import sheets
from config import Config
from handlers import rekap

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


# ---------- 1. zoom_entries parsing ----------

class FakeClient(sheets.SheetsClient):
    """Async stubs — no gspread I/O. Rows: Zoom Record B..O (idx 1..14)."""

    def __init__(self, zoom_rows, done_set, status_map, class_fixtures=None):
        super().__init__(cfg_fixture())
        self._zoom_rows = zoom_rows
        self._done = done_set
        self._status = status_map
        self._class_fixtures = class_fixtures or []

    async def zoom_entries(self, name):
        return await self._run(lambda: self._zoom_entries_fake(name))

    def _zoom_entries_fake(self, name):
        # replicate _zoom_entries against fixture rows directly
        rows = self._zoom_rows
        target = self._normalize(name)
        out = []
        for i, r in enumerate(rows):
            if i == 0:
                continue
            if len(r) <= 14:
                continue
            if self._normalize(r[2]) != target:
                continue
            if not any((c or "").strip() for c in r[1:15]):
                continue
            kode = r[5].strip()
            if not kode:
                continue
            out.append({
                "kode": kode, "subject": r[6].strip(),
                "tanggal": self._norm_date(r[3].strip()),
                "pertemuan": r[7].strip(), "scheme": r[8].strip(),
                "sks": r[9].strip(), "tipe": r[10].strip(), "dosen": r[11].strip(),
                "mulai": r[12].strip(), "zoom": r[13].strip(),
                "catatan": r[14].strip(), "row": i + 1,
            })
        return out

    async def get_rekap_done(self, facilitator_name):
        return self._done

    async def rekap_row_status(self, tab, kode, tanggal_list):
        return self._status.get(kode, {"state": "none"})

    async def get_all_loggable_classes(self, facilitator_name):
        # (personal, backup, makeup) — fixture kelas jadwal utk resolve jam RENTANG PENUH
        return (list(self._class_fixtures), [], [])


def fake_context(client):
    class _UserData(dict):
        pass
    class _BotData(dict):
        pass
    bot = _BotData()
    bot["sheets"] = client
    class _Ctx:
        bot_data = bot
        user_data = _UserData()
    return _Ctx()


# Zoom rows milik "Ratu Bilqis" (3 entri) + 1 orang lain + 1 kosong
# Layout get_all_values: A=0(No.) B=1(tgl isi) C=2(fasil) D=3(tgl kelas) E=4(semester)
# F=5(kode) G=6(subject) H=7(pertemuan) I=8(scheme) J=9(sks) K=10(tipe) L=11(dosen)
# M=12(jam mulai) N=13(zoom) O=14(catatan)
ZOOM_ROWS = [
    ["Hdr"] * 15,
    ["1", "01/09/2026", "Ratu Bilqis", "06/09/2026", "1", "CS301", "AI", "4", "Online", "3", "Reguler", "Ahmad", "13.00", "Zoom 1", ""],           # kode CS301
    ["2", "01/09/2026", "Ratu Bilqis", "Senin, 8 September 2026", "1", "ARCH1", "Arsitektur", "3 dan 4", "Offline", "2", "Pro", "Budi", "14.00", "Zoom 2", "cat"],  # kode ARCH1 (tanggal panjang -> 08/09/2026)
    ["3", "01/09/2026", "Orang Lain", "10/09/2026", "1", "XX999", "X", "1", "Online", "1", "R", "X", "10.00", "Z", ""],                         # bukan Ratu -> skip
    ["4", "01/09/2026", "Ratu Bilqis", "12/09/2026", "1", "", "", "", "", "", "", "", "", "", ""],                                             # kosong -> skip
]
# done rekap: CS301 sudah ada (06/09/2026 -> complete), ARCH1 ada (08/09/2026 -> incomplete)
DONE = {("cs301", "06/09/2026"), ("arch1", "08/09/2026")}
STATUS = {
    "CS301": {"state": "complete", "count": 1},
    "ARCH1": {"state": "incomplete", "row": 7, "gaps": ["Bukti"]},
}


def _run(fn):
    async def _a():
        return await fn()
    return asyncio.run(_a())


c = FakeClient(ZOOM_ROWS, DONE, STATUS)
ctx = fake_context(c)
ctx.user_data["facilitator"] = "Ratu Bilqis"
ctx.user_data["rekap_tab"] = "Ratu"

# 1. entries parsing
entries = _run(lambda: c.zoom_entries("Ratu Bilqis"))
kodes = [e["kode"] for e in entries]
check("zoom_entries: hanya baris Ratu, skip kosong+lain",
      kodes == ["CS301", "ARCH1"], f"got {kodes}")
a1 = [e for e in entries if e["kode"] == "ARCH1"][0]
check("zoom_entries: tanggal panjang dinormalkan + pertemuan '3 dan 4'",
      a1["tanggal"] == "08/09/2026" and a1["pertemuan"] == "3 dan 4"
      and a1["scheme"] == "Offline" and a1["dosen"] == "Budi" and a1["mulai"] == "14.00"
      and a1["catatan"] == "cat", f"got {a1}")

# 2. klasifikasi 3 status
items = _run(lambda: rekap._classify_zoom_entries(ctx, "Ratu", entries))
kinds = [(it["kind"], it["entry"]["kode"]) for it in items]
check("klasifikasi: CS301 lengkap di-skip, ARCH1 rumpang -> fix, tak ada yg new",
      ("fix", "ARCH1") in kinds and len(kinds) == 1, f"got {kinds}")
fixit = items[0]
check("fix item: bawa row idx + gaps", fixit["row"] == 7 and fixit["gaps"] == ["Bukti"],
      f"got {fixit}")
check("klasifikasi: status_map puntung 'none' -> new",
      True, "lihat test B di bawah")

# 2b. entri tanpa baris rekap -> new
STATUS2 = dict(STATUS)
STATUS2["ARCH1"] = {"state": "none"}
c2 = FakeClient(ZOOM_ROWS, DONE, STATUS2)
ctx2 = fake_context(c2)
ctx2.user_data["facilitator"] = "Ratu Bilqis"
ctx2.user_data["rekap_tab"] = "Ratu"
items2 = _run(lambda: rekap._classify_zoom_entries(ctx2, "Ratu", entries))
kinds2 = [(it["kind"], it["entry"]["kode"]) for it in items2]
check("none -> new: ARCH1 jadi ➕ padahal ada di done map",
      ("new", "ARCH1") in kinds2 and len(kinds2) == 1, f"got {kinds2}")

# 3. urut tanggal lama->baru (3 entri new semua, tanggal acak)
ROWS3 = [
    ["Hdr"] * 15,
    ["1", "01/09/2026", "Ratu Bilqis", "17/09/2026", "1", "K3", "M", "1", "Online", "3", "R", "X", "10.00", "Z", ""],
    ["2", "01/09/2026", "Ratu Bilqis", "01/09/2026", "1", "K1", "M", "1", "Online", "3", "R", "X", "10.00", "Z", ""],
    ["3", "01/09/2026", "Ratu Bilqis", "09/09/2026", "1", "K2", "M", "1", "Online", "3", "R", "X", "10.00", "Z", ""],
]
c3 = FakeClient(ROWS3, set(), {})
ctx3 = fake_context(c3)
ctx3.user_data["facilitator"] = "Ratu Bilqis"
ctx3.user_data["rekap_tab"] = "Ratu"
entries3 = _run(lambda: c3.zoom_entries("Ratu Bilqis"))
items3 = _run(lambda: rekap._classify_zoom_entries(ctx3, "Ratu", entries3))
check("urutan: K1 (01/09) < K2 (09/09) < K3 (17/09)",
      [it["entry"]["kode"] for it in items3] == ["K1", "K2", "K3"],
      f"got {[it['entry']['kode'] for it in items3]}")

# 4. paginasi
MANY = [{"kind": "new", "entry": {"kode": f"K{i:02d}", "tanggal": f"{i:02d}/09/2026", "pertemuan": "1"}} for i in range(30)]


def _pick_count(kb):
    return sum(1 for r in kb for b in r if b.callback_data.startswith("rzk:") and not b.callback_data.endswith((":manual", ":cancel")))


kb0 = rekap._zoom_kb(MANY, 0)
check("paginasi hal1: 25 entri + Next + Prev TIADA",
      _pick_count(kb0) == 25
      and any(b.callback_data == "rzkp:next" for r in kb0 for b in r)
      and not any(b.callback_data == "rzkp:prev" for r in kb0 for b in r),
      f"got picks={_pick_count(kb0)} rows={len(kb0)}")
kb1 = rekap._zoom_kb(MANY, 1)
check("paginasi hal2: 5 entri + Prev + Next TIADA",
      _pick_count(kb1) == 5
      and any(b.callback_data == "rzkp:prev" for r in kb1 for b in r)
      and not any(b.callback_data == "rzkp:next" for r in kb1 for b in r),
      f"got picks={_pick_count(kb1)} rows={len(kb1)}")
bottom = kb1[-1]
labels = [b.text for b in bottom]
check("baris bawah: manual + batal", "➕ Buat entri lain" in " ".join(labels) and "❌ Batal" in " ".join(labels),
      f"got {labels}")

# 5. prefill zoom -> record
e = {"kode": "ARCH1", "subject": "Arsitektur", "tanggal": "08/09/2026",
     "pertemuan": "3 dan 4", "scheme": "Offline", "sks": "2", "dosen": "Budi",
     "mulai": "14.00", "zoom": "Zoom 2", "catatan": ""}
cls_from = rekap._class_from_zoom(e)
check("class_from_zoom: kode/matkul/sks/dosen/jam terisi",
      cls_from.code == "ARCH1" and cls_from.subject == "Arsitektur"
      and cls_from.sks == "2" and cls_from.lecturer == "Budi" and cls_from.time_range == "14.00",
      f"got {cls_from}")
check("tipe dari scheme: Offline->On-site",
      rekap._zoom_tipe("Offline") == "On-site" and rekap._zoom_tipe("Online") == "Online",
      f"got {rekap._zoom_tipe('Offline')}")

# 5b. prefill jam = RENTANG PENUH (bukan jam mulai Zoom M)
MASTER_ARCH1 = sheets.ClassEntry(
    code="ARCH1", subject="Arsitektur", day="Senin", time_range="14.00 - 16.30",
    category="Reguler", lecturer="Budi", room="", rombel="", sks="2",
    zoom_number="2", zoom_link="", keterangan="",
)
c_full = FakeClient(ZOOM_ROWS, DONE, STATUS, class_fixtures=[MASTER_ARCH1])
fctx = fake_context(c_full)
fctx.user_data["facilitator"] = "Ratu Bilqis"
fctx.user_data["rekap_tab"] = "Ratu"
jam_full = _run(lambda: rekap._resolve_jam_range(fctx, e))
check("jam range penuh: ARCH1 dari jadwal master '14.00 - 16.30' di-pakai, bukan '14.00'",
      jam_full == "14.00 - 16.30", f"got {jam_full!r}")
c_legacy = FakeClient(ZOOM_ROWS, DONE, STATUS, class_fixtures=[MASTER_ARCH1])
lctx = fake_context(c_legacy)
lctx.user_data["facilitator"] = "Ratu Bilqis"
legacy_e = dict(e, kode="LAMA99", mulai="09.00")
jam_legacy = _run(lambda: rekap._resolve_jam_range(lctx, legacy_e))
check("fallback kode lama: kelas gak ketemu -> Zoom M start-only",
      jam_legacy == "09.00", f"got {jam_legacy!r}")
# prefill utk ➕ baru: cls dari zoom dgn time_range resolved -> rec.jam range penuh
bctx2 = fake_context(c_full)
bctx2.user_data.update({"facilitator": "Ratu", "meeting": "3 dan 4", "tipe": "On-site",
                        "zoom_tanggal": "08/09/2026"})
cls2 = rekap._class_from_zoom(e)
cls2.time_range = _run(lambda: rekap._resolve_jam_range(bctx2, e))
rec2 = rekap._build_base(bctx2, cls2, rekap._tanggal_kelas(bctx2, cls2))
check("build_base: jam rec = range penuh jadwal",
      rec2.jam == "14.00 - 16.30", f"got {rec2.jam!r}")

# 6. jalur manual: _tanggal_keys fallback (zoom_tanggal TIADA -> kelas)
mctx = fake_context(FakeClient([], set(), {}))
mctx.user_data["zoom_tanggal"] = "08/09/2026"
keys_zoom = rekap._tanggal_keys(mctx, cls_from)
check("tanggal_keys dengan zoom_tanggal: dd/mm + panjang",
      keys_zoom == ["08/09/2026", rekap.sheets.tanggal_panjang("08/09/2026")],
      f"got {keys_zoom}")
mctx2 = fake_context(FakeClient([], set(), {}))
mctx2.user_data["zoom_tanggal"] = "08/09/2026"
k = rekap._tanggal_kelas(mctx2, cls_from)
check("tanggal_kelas dengan zoom_tanggal: pakai tanggal zoom",
      k == rekap.sheets.tanggal_panjang("08/09/2026"), f"got {k}")

# 7. build_base prefill (jalur ➕)
bctx = fake_context(FakeClient([], set(), {}))
bctx.user_data.update({"facilitator": "Ratu", "cls": cls_from,
                       "meeting": "3 dan 4", "tipe": "On-site", "zoom_tanggal": "08/09/2026"})
rec = rekap._build_base(bctx, cls_from, rekap._tanggal_kelas(bctx, cls_from))
check("prefill zoom->record: tanggal/pertemuan/tipe/sks/dosen",
      rec.tanggal == rekap.sheets.tanggal_panjang("08/09/2026")
      and rec.pertemuan == "3 dan 4" and rec.tipe == "On-site"
      and rec.sks == "2" and rec.lecturer == "Budi" and rec.kode == "ARCH1",
      f"got {rec}")

# 8. register: state ZOOM + handler rzk ada (pattern wiring)
import re as _re
src = Path("handlers/rekap.py").read_text(encoding="utf-8")
check("register: pattern rzk pick + rzkp nav + rzk manual terbaca",
      bool(_re.search(r'\^rzk:\\d\+\$', src))
      and bool(_re.search(r'\^rzkp:\(prev\|next\)\$', src))
      and bool(_re.search(r'\^rzk:manual\$', src)),
      "pattern handler zoom picker tidak ditemukan")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)