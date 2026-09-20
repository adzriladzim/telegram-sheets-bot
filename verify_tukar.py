"""Offline verification — /tukar jadwal fasil (tanpa approval).

Covers: pola sekali/tetap/jam, rebutan "catat duluan menang", undo
/tukar_batal, no-dobel-progress, merge read-side (get_classes baca swap dulu),
allow_reentry + html.escape + WIB di handler.

Run: py verify_tukar.py   (no creds, no Google — FakeClient stub)
"""
from __future__ import annotations

import asyncio
import re
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import sheets
from config import Config

PASS, FAIL = [], []


def check(label, cond, msg=""):
    (PASS if cond else FAIL).append((label, msg))
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))


def cfg_for() -> Config:
    return Config(
        bot_token="x", sheet_id="s", service_account_json=Path("secrets/none.json"),
        facilitator_name="", master_sheet="M", zoom_record_sheet="Z",
        backup_sheet="B", cancel_sheet="C", tukar_sheet="T",
        absen_sheet_id="a", absen_sheet_name="A",
        rekap_sheet_id="r", rekap_bukti_folder_id="", semester="1",
        reminder_slots=((21, 0),), reminder_enabled=False,
        heartbeat_hour=22, heartbeat_minute=0, heartbeat_enabled=False)


def master_row(code, fasil="", day="Senin", jam="13.00 - 15.30", mk=None):
    mk = mk or ("MK" + code)
    row = [""] * 14
    row[sheets.COL_DAY] = day
    row[sheets.COL_FASIL] = fasil
    row[sheets.COL_KATEGORI] = "Reguler"
    row[sheets.COL_JAM] = jam
    row[sheets.COL_KODE] = code
    row[sheets.COL_MK] = mk
    row[sheets.COL_DOSEN] = "Dosen X"
    row[sheets.COL_RUANG] = "R.101"
    row[sheets.COL_ROMBEL] = "2 Ilkom"
    row[sheets.COL_SKS] = "3"
    row[sheets.COL_ZOOM_NO] = "9"
    row[sheets.COL_ZOOM_LINK] = ""
    row[sheets.COL_KETERANGAN] = ""
    return row


def ce(code, day="Senin", subject=None, time_range="13.00 - 15.30"):
    subject = subject or ("MK" + code)
    return sheets.ClassEntry(
        code=code, subject=subject, day=day, time_range=time_range,
        category="Reguler", lecturer="Dosen", room="R.101", rombel="2 Ilkom",
        sks="3", zoom_number="9", zoom_link="")


class FakeWs:
    def __init__(self, client, title, rows, cols):
        self.client = client
        self.title = title
        self.row_count = rows
        self.col_count = cols
        self.writes = []

    def update(self, rng, values, value_input_option="USER_ENTERED"):
        m = re.match(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", rng)
        assert m, f"bad range {rng}"
        c0 = sheets.SheetsClient._col_num(m.group(1)) - 1
        r0 = int(m.group(2))
        rows = self.client.rows_by_title[self.title]
        for i, row in enumerate(values):
            idx = r0 - 1 + i
            while len(rows) <= idx:
                rows.append([""] * sheets.SheetsClient._col_num("K"))
            for j, v in enumerate(row):
                ci = c0 + j
                rowdata = list(rows[idx])
                while len(rowdata) <= ci:
                    rowdata.append("")
                rowdata[ci] = str(v or "")
                rows[idx] = rowdata
        self.writes.append((rng, values))

    def batch_clear(self, ranges):
        for rng in ranges:
            m = re.match(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", rng)
            c0 = sheets.SheetsClient._col_num(m.group(1)) - 1
            c1 = sheets.SheetsClient._col_num(m.group(3)) - 1
            r0, r1 = int(m.group(2)), int(m.group(4))
            rows = self.client.rows_by_title[self.title]
            for i in range(r0 - 1, min(r1, len(rows))):
                for ci in range(c0, min(c1 + 1, len(rows[i]))):
                    rows[i][ci] = ""
        self.writes.append(("clear", ranges))


class FakeClient(sheets.SheetsClient):
    """Offline stub: rows_by_title sebagai satu-satunya sumber + write-through."""

    def __init__(self, swap_rows=(), master_rows=()):
        super().__init__(cfg_for())
        header = list(sheets.SheetsClient._SWAP_HEADER)
        self.rows_by_title = {
            "M": [master_row("HDR")] + [list(r) for r in master_rows],
            "T": [header] + [list(r) for r in swap_rows],
        }

    def _cached_rows(self, ss_id, title):
        return self.rows_by_title.get(title, [])

    def _sheet(self, title):
        return FakeWs(self, title, 200, 11)

    def _ensure_swap_sheet(self, create=False):
        if "T" not in self.rows_by_title:
            self.rows_by_title["T"] = [list(sheets.SheetsClient._SWAP_HEADER)]
        return True


def sw(no="2", oleh="A", kode_saya="X", tanggal="25/09/2026", dengan="B",
       kode_dia="Y", pola="sekali", jam="", status="AKTIF", catatan="", dicatat="2026-09-21 10:00"):
    return [no, dicatat, oleh, kode_saya, tanggal, dengan, kode_dia, pola, jam, status, catatan]


# ---------- 1. normalisasi tanggal ----------
fc0 = FakeClient()
check("_norm_tanggal '6/9/2026' -> 06/09/2026", fc0._norm_tanggal("6/9/2026") == "06/09/2026",
      f"got {fc0._norm_tanggal('6/9/2026')!r}")
check("_norm_tanggal sampah -> ''", fc0._norm_tanggal("besok") == "", fc0._norm_tanggal("besok"))

# ---------- 2. SwapRecord.as_row ----------
r = sheets.SwapRecord(no="7", dicatat="t", oleh="A", kode_saya="X", tanggal="25/09/2026",
                      dengan="B", kode_dia="Y", pola="jam", jam="13.00 - 15.30")
row = r.as_row()
check("as_row 11 kolom A..K", len(row) == 11 and row[0] == "7" and row[7] == "jam"
      and row[8] == "13.00 - 15.30" and row[9] == "AKTIF", f"got {row}")

# ---------- 3. conflict first-wins (catat duluan menang) ----------
fc = FakeClient(swap_rows=[sw()])
recs = fc._swap_records(fc._cached_rows("s", "T"))
check("_swap_records baca header dilewati (1 record)", len(recs) == 1, f"got {len(recs)}")
same_day = fc._swap_conflict(recs, "X", "Z", "25/09/2026", "sekali")
check("sekali X@25/09 vs sekali X@25/09 -> conflict", same_day is not None,
      "seharusnya ditolak (catat duluan menang)")
diff_day = fc._swap_conflict(recs, "X", "Z", "26/09/2026", "sekali")
check("sekali X@25/09 vs sekali X@26/09 -> BUKAN conflict", diff_day is None,
      f"got {diff_day}")
tetap_vs_ada = fc._swap_conflict(recs, "X", "Z", "", "tetap")
check("tetap X vs sekali X existing -> conflict", tetap_vs_ada is not None)
ada_vs_tetap = fc._swap_conflict(recs, "Z", "X", "25/09/2026", "sekali")
check("sekali tsb vs tetap-existing-X -> conflict", fc._swap_conflict(
    recs, "Z", "X", "25/09/2026", "tetap") is not None)
no_collide = fc._swap_conflict(recs, "W", "Q", "25/09/2026", "sekali")
check("kode beda -> BUKAN conflict", no_collide is None)

# ---------- 4. append: rebutan + no-dobel-progress ----------
fc_a = FakeClient()
rec1 = sheets.SwapRecord(no="", dicatat="t", oleh="A", kode_saya="X", tanggal="25/09/2026",
                         dengan="B", kode_dia="Y", pola="sekali")
no1 = fc_a._append_swap(rec1)
check("append pertama sukses (no #1)", no1 == "1", f"got {no1!r}")
rec2 = sheets.SwapRecord(no="", dicatat="t", oleh="C", kode_saya="X", tanggal="25/09/2026",
                         dengan="B", kode_dia="Y", pola="sekali")
try:
    fc_a._append_swap(rec2)
    append_second = "no-error"
except sheets.SheetsError as exc:
    append_second = str(exc)
check("append kedua (rebutan sama kode+tanggal) ditolak",
      "Sudah ada tukar" in append_second, f"got {append_second!r}")
rec3 = sheets.SwapRecord(no="", dicatat="t", oleh="C", kode_saya="X", tanggal="26/09/2026",
                         dengan="B", kode_dia="Y", pola="sekali")
try:
    no3 = fc_a._append_swap(rec3)
    append_other_day = f"ok:{no3}"
except sheets.SheetsError as exc:
    append_other_day = "err"
check("append tanggal beda boleh (X@26/09)", append_other_day.startswith("ok:"), f"got {append_other_day}")
rec_tetap = sheets.SwapRecord(no="", dicatat="t", oleh="C", kode_saya="X", tanggal="",
                              dengan="B", kode_dia="Y", pola="tetap")
try:
    fc_a._append_swap(rec_tetap)
    append_tetap_dup = "no-error"
except sheets.SheetsError as exc:
    append_tetap_dup = str(exc)
check("tetap X saat sudah ada swap X -> ditolak",
      "Sudah ada tukar" in append_tetap_dup, f"got {append_tetap_dup!r}")
check("no-dobel-progress: total baris tukar = 2 (bukan 3/4)",
      len(fc_a._cached_rows("s", "T")) == 3, f"got rows={len(fc_a._cached_rows('s', 'T'))}")

# ---------- 5. merge read-side: sekali/tetap/jam ----------
my_classes = [ce("X"), ce("W")]
fc_sekali = FakeClient(swap_rows=[sw()], master_rows=[master_row("X", "A"), master_row("Y", "B")])
merged = fc_sekali._merged_classes("A", [ce("X"), ce("W")])
codes = [c.code for c in merged]
check("sekali: X dilepas tanggal belum lewat", "X" not in codes, f"got {codes}")
check("sekali: Y diterima (category Tukar + swap_tanggal)",
      any(c.code == "Y" and c.category == "Tukar" and c.swap_tanggal == "25/09/2026" for c in merged),
      f"got {[(c.code, c.category, c.swap_tanggal) for c in merged]}")
mereg = fc_sekali._merged_classes("A", [ce("X")])
check("sekali: kelas lain (W) tak tersentuh", any(c.code == "W" for c in merged))

fc_tetap = FakeClient(swap_rows=[sw(pola="tetap", tanggal="")],
                      master_rows=[master_row("X", "A"), master_row("Y", "B")])
mt = fc_tetap._merged_classes("A", [ce("X"), ce("W")])
check("tetap: X dilepas permanen", all(c.code != "X" for c in mt), f"got {[c.code for c in mt]}")
check("tetap: Y diterima permanen", any(c.code == "Y" and c.category == "Tukar" for c in mt))
mt_b = fc_tetap._merged_classes("B", [ce("Y")])
check("tetap: sisi B dapat X (bilateral)", any(c.code == "X" for c in mt_b), f"got {[c.code for c in mt_b]}")

fc_jam = FakeClient(swap_rows=[sw(pola="jam", jam="13.00 - 15.30")],
                    master_rows=[master_row("X", "A"), master_row("Y", "B")])
mj = fc_jam._merged_classes("A", [ce("X")])
check("jam: seperti sekali (date-scoped) — X dilepas, Y diterima",
      all(c.code != "X" for c in mj) and any(c.code == "Y" for c in mj), f"got {[c.code for c in mj]}")

fc_past = FakeClient(swap_rows=[sw(tanggal="01/01/2020")],
                     master_rows=[master_row("X", "A"), master_row("Y", "B")])
mp = fc_past._merged_classes("A", [ce("X")])
check("sekali lewat: X balik, Y tidak ditambahkan",
      any(c.code == "X" for c in mp) and all(c.code != "Y" for c in mp),
      f"got {[c.code for c in mp]}")

# ---------- 6. undo /tukar_batal + riwayat ----------
fc_u = FakeClient(swap_rows=[sw()], master_rows=[master_row("X", "A"), master_row("Y", "B")])
check("sebelum batal: X tidak muncul utk A",
      all(c.code != "X" for c in fc_u._merged_classes("A", [ce("X")])))
res = fc_u._cancel_swap("2", "A", "batalkan by chat")
check("_cancel_swap id ada -> 1", res == 1, f"got {res}")
recs_u = fc_u._swap_records(fc_u._cached_rows("s", "T"))
check("undo: status jadi BATAL (riwayat tetap ada)", recs_u[0]["status"] == "BATAL",
      f"got {recs_u[0]['status']!r}")
check("undo: kelas X balik ke A", any(c.code == "X" for c in fc_u._merged_classes("A", [ce("X")])))
res2 = fc_u._cancel_swap("2", "A")
check("undo dua kali -> -1 (sudah BATAL)", res2 == -1, f"got {res2}")
check("undo id tak ada -> 0", fc_u._cancel_swap("999", "A") == 0, "got nonzero")

# ---------- 6b. S2: banding tanggal pakai date, bukan string ----------
fc_p = FakeClient()
check("_parse_tanggal valid -> date", fc_p._parse_tanggal("25/09/2026") == date(2026, 9, 25),
      f"got {fc_p._parse_tanggal('25/09/2026')!r}")
check("_parse_tanggal tidak valid (31/02) -> None", fc_p._parse_tanggal("31/02/2026") is None,
      f"got {fc_p._parse_tanggal('31/02/2026')!r}")
check("_parse_tanggal sampah -> None", fc_p._parse_tanggal("besok") is None,
      f"got {fc_p._parse_tanggal('besok')!r}")

fc_past_raw = FakeClient(swap_rows=[sw(tanggal="6/9/2020")],
                         master_rows=[master_row("X", "A"), master_row("Y", "B")])
mp_raw = fc_past_raw._merged_classes("A", [ce("X")])
check("tanggal lewat tidak-default-pad (6/9/2020): X balik, Y tak ditambahkan",
      any(c.code == "X" for c in mp_raw) and all(c.code != "Y" for c in mp_raw),
      f"got {[c.code for c in mp_raw]} (string-compare lama salah: '6/' > '2/' -> X dilepas)")

fc_fut_raw = FakeClient(swap_rows=[sw(tanggal="6/9/2099")],
                        master_rows=[master_row("X", "A"), master_row("Y", "B")])
mf_raw = fc_fut_raw._merged_classes("A", [ce("X")])
check("tanggal depan tidak-default-pad (6/9/2099): X dilepas, Y diterima",
      all(c.code != "X" for c in mf_raw) and any(c.code == "Y" for c in mf_raw),
      f"got {[c.code for c in mf_raw]}")

# ---------- 6c. S3: cancel atomik (lookup + otorisasi + cancel satu _run) ----------
def run_cso(fc, row_id, by, is_admin):
    return asyncio.run(fc.cancel_swap_owned(row_id, by, is_admin))

fc_own = FakeClient(swap_rows=[sw()], master_rows=[master_row("X", "A")])
res = run_cso(fc_own, "2", "Z", is_admin=False)
check("cancel_swap_owned bukan pencatat -> (-2, owner)", res == (-2, "A"), f"got {res}")
fc_adm = FakeClient(swap_rows=[sw()], master_rows=[master_row("X", "A")])
res = run_cso(fc_adm, "2", "Z", is_admin=True)
check("cancel_swap_owned admin bypass -> (1, owner)", res == (1, "A"), f"got {res}")
fc_own2 = FakeClient(swap_rows=[sw()], master_rows=[master_row("X", "A")])
res = run_cso(fc_own2, "2", "A", is_admin=False)
check("cancel_swap_owned pemilik -> (1, owner)", res == (1, "A"), f"got {res}")
res = run_cso(fc_own2, "2", "A", is_admin=False)
check("cancel_swap_owned dua kali -> (-1, owner)", res == (-1, "A"), f"got {res}")
fc_na = FakeClient(swap_rows=[sw()], master_rows=[master_row("X", "A")])
res = run_cso(fc_na, "999", "A", is_admin=False)
check("cancel_swap_owned id tak ada -> (0, '')", res == (0, ""), f"got {res}")

# ---------- 7. handler hygiene: allow_reentry, html.escape, WIB ----------
src = Path("handlers/tukar.py").read_text(encoding="utf-8")
check("allow_reentry=True terpasang di conv /tukar", "allow_reentry=True" in src,
      "allow_reentry tidak ketemu")
check("confirm_cb pakai lock for_chat", "for_chat(" in src, "for_chat tidak ketemu")
check("waktu dicatat WIB (datetime.now(sheets.WIB))", "datetime.now(sheets.WIB)" in src,
      "WIB tidak dipakai")
check("html.escape dipakai di semua reply user data", "html.escape(" in src, "html.escape tidak ada")
check("get_classes baca swap dulu (_merged_classes dipanggil)",
      "self._merged_classes" in Path("sheets.py").read_text(encoding="utf-8"),
      "_merged_classes tidak terhubung di get_classes")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for label, msg in FAIL:
    print(f"  FAIL: {label} — {msg}")
sys.exit(1 if FAIL else 0)