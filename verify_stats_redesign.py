"""Offline stub — /stats redesign + absen coverage pengisi-row fix. No live creds.

Run: py verify_stats_redesign.py   (expect: ALL PASS)

Checks:
  1. _absen_coverage skips the filler-name header row (WDC05-like block): a
     row with names only (no NIM/Nama) must NOT count as a filled meeting,
     and a block with only a pengisi name (no student status) stays 0/16.
  2. _parse_args: 'detail' -> detail-mode flag + period.
  3. _build_summary renders ringkas sections (Aktif, Sudah-log, Perlu
     perhatian, Beres semua) with escaped names.
  4. _build_detail renders arrears grouped per fasil + coverage macet only.
  5. _chunks keeps every summary/detail chunk <=3500 bytes & HTML-parseable.
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import handlers.stats as stats
import sheets
from config import Config
from telegram.constants import ParseMode

PASS, FAIL = [], []


def check(label, cond, msg=""):
    (PASS if cond else FAIL).append((label, msg))
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))


# ---------- fake gspread layer ----------
class FakeClient:
    def __init__(self, spreadsheets):
        self.spreadsheets = spreadsheets

    def open_by_key(self, ss_id):
        return self.spreadsheets[ss_id]


class FakeWorksheet:
    def __init__(self, title, rows):
        self.title = title
        self.rows = rows

    def get_all_values(self):
        return [list(r) for r in self.rows]


class FakeSpreadsheet:
    def __init__(self, ss_id, worksheets):
        self.id = ss_id
        self._ws = {w.title: w for w in worksheets}

    def worksheet(self, title):
        return self._ws[title]

    def worksheets(self):
        return list(self._ws.values())

    def values_batch_get(self, ranges):
        out = []
        for rng in ranges:
            m = re.match(r"'([^']+)'", rng)
            out.append({"values": self._ws[m.group(1)].get_all_values()})
        return {"valueRanges": out}


def fresh_client():
    cfg = Config(
        bot_token="x", sheet_id="ss_main", service_account_json=Path("secrets/none.json"),
        facilitator_name="", master_sheet="Master", zoom_record_sheet="Zoom Record",
        backup_sheet="Backup", cancel_sheet="Cancel", absen_sheet_id="ss_absen",
        absen_sheet_name="Absen", rekap_sheet_id="ss_rekap", rekap_bukti_folder_id="",
        semester="1", reminder_slots=(), reminder_enabled=False,
        heartbeat_hour=22, heartbeat_minute=0, heartbeat_enabled=False,
    )
    c = sheets.SheetsClient(cfg)
    c._gc = FakeClient({})
    for cache in (sheets._rows_cache, sheets._tabs_cache, sheets._absen_students_cache,
                  sheets._absen_kodes_cache, sheets._classes_cache, sheets._rekap_status_cache,
                  sheets._rekap_tab_cache, sheets._feedback_cache):
        cache.clear()
    return c


# ---------- 1. coverage: pengisi-name row must NOT count ----------
# Real sheet layout: Kode Kelas row, NIM header, session-number row, filler-name
# row (no NIM), blank, students... (see live dump for WDC05-ish blocks).
absen_tab = FakeWorksheet("Computer Science", [
    ["JADWAL ABSEN HARI 1"],
    ["Kode Kelas", "WDC05", "", "SF", "legend"],
    ["NIM", "Nama Mahasiswa", "Mode Kelas Asal", "Sesi Pertemuan yang Diikuti", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "Presensi"],
    ["", "", "", "1", "2", "3", "4"],
    ["", "", "", "Zazkia Nur Alifa", "Zazkia Nur Alifa", "Zazkia Nur Alifa"],
    [],
    ["24111600001", "ADIKTO HUTABALIAN", "OFFLINE", "O", "O"],
    ["24111600002", "BUDI SANTOSO", "OFFLINE", "O"],
    ["Program Studi", "S1 - Computer Science"],
    ["Kode Kelas", "DS01", "", "SF", "legend"],
    ["NIM", "Nama Mahasiswa", "Mode Kelas Asal", "Sesi Pertemuan"],
    ["", "", "", "1", "2"],
    ["", "", "", "", "Pengisi Saja"],
    [],
    ["24111600003", "CITRA AYU", "OFFLINE"],
    ["Program Studi", "S1 - Computer Science"],
])
c = fresh_client()
c._gc.spreadsheets["ss_absen"] = FakeSpreadsheet("ss_absen", [absen_tab])
cover = c._absen_coverage()
check("coverage WDC05 = {1,2} (pengisi row skipped)",
      cover.get("WDC05") == {1, 2}, f"got {cover.get('WDC05')!r}")
check("coverage DS01 = empty (name-only row not a meeting)",
      cover.get("DS01") in (None, set()), f"got {cover.get('DS01')!r}")

# ---------- 2. arg parse ----------
d, days = stats._parse_args(["detail"])
check("_parse_args detail -> detail", d is True, f"got {d}")
d, days = stats._parse_args(["minggu"])
check("_parse_args period default not detail", d is False and days == 7, f"got {d},{days}")
d, days = stats._parse_args(["detail", "bulan"])
check("_parse_args detail+bulan", d is True and days == 30, f"got {d},{days}")
check("_parse_args none", stats._parse_args(None) == (False, 7), f"got {stats._parse_args(None)}")

# ---------- 3+4. render ----------
names = ["Adzril", "Budi", "Citra", "<Bold>"]
data = [{"name": "Adzril", "action": "zoom", "ts": "2026-09-17T08:00:00+07:00"},
        {"name": "Adzril", "action": "absen", "ts": "2026-09-16T08:00:00+07:00"},
        {"name": "Budi", "action": "rekap", "ts": "2026-09-15T08:00:00+07:00"}]
pdata = stats._filter_period(data, 7)
per_menu = Counter(e["action"] for e in pdata)
per_user = defaultdict(Counter)
last_ts = {}
for e in pdata:
    per_user[e["name"]][e["action"]] += 1
    last_ts[e["name"]] = e["ts"]
matriks = [("Adzril", [("ILaw1", "✗", "○")]), ("Budi", [("Comp1", "✓", "○")])]
def _ce(code):
    return type("CE", (), {"code": code})()


arrears = [("Adzril", _ce("ILaw1"), "Senin 8 September 2026", None, "08/09/2026"),
           ("Adzril", _ce("AsDs5"), "Selasa 9 September 2026", None, "09/09/2026")]
cov = {"WDC05": {1, 2}, "DS01": set(), "Arch2": set()}

S = stats._build_summary(names, data, pdata, 7, per_menu, per_user, last_ts,
                         matriks, arrears, cov)
S_JOIN = "\n".join(S)
check("summary has Aktif header", "Aktif 2/4 fasil" in S_JOIN, S_JOIN.splitlines()[1] if len(S_JOIN) else "")
check("summary has Sudah-log %", "Sudah-log 50% kelas" in S_JOIN, f"got {S_JOIN.splitlines()[2] if len(S)>2 else ''!r}")
check("summary has Perlu perhatian + tertua",
      "Perlu perhatian" in S_JOIN and "tertua 08/09" in S_JOIN, S_JOIN)
check("summary Beres semua count", "Beres semua</b> (3" in S_JOIN, S_JOIN.splitlines()[-4] if len(S) > 4 else "")
check("summary escaped name", "&lt;Bold&gt;" in S_JOIN and "<Bold>" not in S_JOIN, S_JOIN)

D = stats._build_detail(names, data, pdata, 7, per_menu, per_user, last_ts,
                        matriks, arrears, cov)
D_JOIN = "\n".join(D)
check("detail has roster", "Roster semua fasil" in D_JOIN, D_JOIN[:80])
check("detail arrears grouped + date once",
      "ILaw1,AsDs5 (2)" in D_JOIN and "📅 Senin 8 September 2026: ILaw1" in D_JOIN, D_JOIN)
check("detail coverage macet only (DS01/Arch2 listed, WDC05 not)",
      "DS01: 0/16" in D_JOIN and "Arch2: 0/16" in D_JOIN and "WDC05" not in D_JOIN, D_JOIN)

# ---------- 5. chunk safety ----------
TAG = re.compile(r"</?[a-zA-Z][^>]*>")


def fake_parse(text):
    rest = TAG.sub("", text)
    if "<" in rest or ">" in rest:
        raise ValueError("stray HTML")


ok_chunks = True
for label, lines in (("summary", S), ("detail", D)):
    for i, (part, pm) in enumerate(stats._chunks(lines)):
        b = stats._b(part)
        if b > 3500 or b >= 4096:
            ok_chunks = False
            print(f"  FAIL chunk {label}[{i}] bytes={b}")
        if pm == ParseMode.HTML:
            try:
                fake_parse(part)
            except ValueError:
                ok_chunks = False
                print(f"  FAIL chunk {label}[{i}] unparseable HTML")
check("summary+detail chunks <=3500 bytes & parseable", ok_chunks)

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL"))
sys.exit(1 if FAIL else 0)