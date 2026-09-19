"""Offline verification — rekap refresh O–S (picker 🔃 + sheets.refresh_os).

Checks:
  1. diff-deteksi: HANYA sel yang berubah di tulis, kolom O..S
  2. tulis cuma O–S — B..L (termasuk Bukti) tak pernah disentuh
  3. 0-diff -> no-op (tidak ada write)
  4. lengkap MUNCUL di picker: kind refresh + row, tombol 🔃 + separator grup
  5. handler pick_zoom_refresh: reply diff, reply no-change, fail-open SheetsError
Run: py verify_rekap_refresh.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import sheets
from config import Config
from handlers import rekap

PASS, FAIL = [], []


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


def run(fn):
    async def _a():
        return await fn()
    return asyncio.run(_a())


class FakeRefreshClient(sheets.SheetsClient):
    """Stub sync intern refresh_os — no gspread I/O. Record semua write."""

    def __init__(self, counts_map, fb_map, cur_row, fail_counts=False):
        super().__init__(cfg_fixture())
        self.counts_map = counts_map
        self.fb_map = fb_map
        self.cur_row = list(cur_row)
        self.writes = []
        self.fail_counts = fail_counts

    def _absen_counts(self, kode, pertemuan):
        if self.fail_counts:
            raise sheets.SheetsError("Kode CS301 tidak ditemukan di sheet Absen (cek 15 prodi).")
        return dict(self.counts_map.get((kode, pertemuan), {
            "total": 0, "hadir": 0, "feedback": 0, "tidak": 0,
            "belum": 0, "izin": 0, "prodi": "SI"}))

    def _feedback_counts(self, kode, pertemuan, dosen, prodi_tab):
        fb = self.fb_map.get((kode, pertemuan.strip()))
        if fb is None:
            raise sheets.SheetsError("feedback gagal")
        return {"q": fb, "schools": {}}

    def _rekap_row_values(self, tab, row_idx):
        return list(self.cur_row)

    def _update_rekap_cells(self, tab, row_idx, cells):
        self.writes.append((tab, row_idx, dict(cells)))


def row_with(o, p, q, r, s):
    """Baris rekap 19 kolom A..S; isi minimal A/B/E + O..S."""
    rw = [""] * 19
    rw[0], rw[1], rw[4] = "1", "06/09/2026", "CS301"
    rw[14], rw[15], rw[16], rw[17], rw[18] = o, p, q, r, s
    return rw


# ---------- 1. diff-deteksi ----------
# Absen: total 32, hadir 30, tidak 2; Feedback q=24 -> S = 32-24 = 8.
c1 = FakeRefreshClient(
    {("CS301", 4): {"total": 32, "hadir": 30, "tidak": 2}},
    {("CS301", "4"): 24},
    row_with("30", "30", "18", "2", "8"),
)
changed1, before1, after1 = run(lambda: c1.refresh_os("ss_rekap", "Ratu", 5, "CS301", "4", "Dosen"))
check("diff: cuma O dan Q yang berubah",
      sorted(changed1) == ["O", "Q"], f"got {changed1}")
check("diff: before/after benar", changed1["O"] == ("30", "32") and changed1["Q"] == ("18", "24"),
      f"got {changed1}")
check("diff: tulis cuma sel yang BEDA (O,Q)",
      len(c1.writes) == 1 and sorted(c1.writes[0][2]) == ["O", "Q"], f"got {c1.writes}")
check("diff: B..L TIDAK disentuh",
      len(c1.writes) == 1 and all(k in "OPQRS" for k in c1.writes[0][2]), f"got {c1.writes}")

# ---------- 2. 0-diff -> no-op ----------
c2 = FakeRefreshClient(
    {("CS301", 4): {"total": 30, "hadir": 30, "tidak": 2}},
    {("CS301", "4"): 18},
    row_with("30", "30", "18", "2", "12"),
)
changed2, _, _ = run(lambda: c2.refresh_os("ss_rekap", "Ratu", 5, "CS301", "4", "Dosen"))
check("0-diff: changed kosong + TIDAK ADA write",
      changed2 == {} and c2.writes == [], f"got {changed2} | writes={c2.writes}")

# ---------- 3. semua kolom beda -> tulis O..S lengkap, tanpa B..L ----------
c3 = FakeRefreshClient(
    {("CS301", 4): {"total": 5, "hadir": 4, "tidak": 1}},
    {("CS301", "4"): 3},
    row_with("3", "2", "9", "5", "9"),
)
changed3, _, _ = run(lambda: c3.refresh_os("ss_rekap", "Ratu", 5, "CS301", "4", "Dosen"))
check("multi-diff: 5 kolom angka berubah",
      sorted(changed3) == ["O", "P", "Q", "R", "S"], f"got {changed3}")
check("multi-diff: write = O..S SAJA (B–L aman)",
      len(c3.writes) == 1 and sorted(c3.writes[0][2]) == ["O", "P", "Q", "R", "S"], f"got {c3.writes}")

# ---------- 4. lengkap MUNCUL di picker (tidak di-skip) ----------
class FakePickerClient(sheets.SheetsClient):
    def __init__(self, status_map):
        super().__init__(cfg_fixture())
        self._status = status_map

    async def get_rekap_done(self, facilitator_name):
        return {("cs301", "06/09/2026")}

    async def rekap_row_status(self, tab, kode, tanggal_list):
        return self._status.get(kode, {"state": "none"})

    async def zoom_entries(self, name):
        return [{"kode": "CS301", "subject": "AI", "tanggal": "06/09/2026",
                 "pertemuan": "4", "scheme": "Online", "sks": "3", "tipe": "Reguler",
                 "dosen": "Dosen", "mulai": "13.00", "zoom": "Z", "catatan": ""}]


class FakeMsg:
    def __init__(self):
        self.text = None
        self.markup = None

    async def reply_text(self, text, **kw):
        self.text = text
        self.markup = kw.get("reply_markup")


pc = FakePickerClient({"CS301": {"state": "complete", "count": 1, "row": 5}})
pctx = SimpleNamespace(
    bot_data={"sheets": pc},
    user_data={"facilitator": "Ratu Bilqis", "rekap_tab": "Ratu"},
)
entries = run(lambda: pc.zoom_entries("Ratu Bilqis"))
items = run(lambda: rekap._classify_zoom_entries(pctx, "Ratu", entries))
check("picker: entri lengkap jadi kind refresh + row",
      len(items) == 1 and items[0]["kind"] == "refresh" and items[0]["row"] == 5,
      f"got {items}")
work = [it for it in items if it["kind"] != "refresh"]
refresh = [it for it in items if it["kind"] == "refresh"]
msg = FakeMsg()
pctx.user_data.update({"zoom_items": work, "zoom_refresh": refresh, "zoom_page": 0})
run(lambda: rekap._render_zoom_picker(msg, pctx))
buttons = [b for row in msg.markup.inline_keyboard for b in row]
check("picker: tombol 🔃 + separator grup",
      any(b.text == "🔃 06/09 CS301 p.4" and b.callback_data == "rzkr:0" for b in buttons)
      and "— 🔃 sudah lengkap (update angka?) —" in msg.text,
      f"got {msg.text!r} | {[b.text for b in buttons]}")


# ---------- 5. handler pick_zoom_refresh ----------
class _Msg:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


class _Q:
    def __init__(self, data):
        self.data = data
        self.answered = False
        self.message = _Msg()

    async def answer(self):
        self.answered = True


async def _pick(data, client, refresh):
    q = _Q(data)
    update = SimpleNamespace(callback_query=q, effective_chat=SimpleNamespace(id=1))
    ctx = SimpleNamespace(
        bot_data={"sheets": client},
        user_data={"zoom_refresh": refresh, "rekap_tab": "Ratu", "facilitator": "Ratu Bilqis"},
    )
    with patch.object(rekap.usage, "log") as mlog:
        state = await rekap.pick_zoom_refresh(update, ctx)
    return state, q, mlog


REFRESH_ITEMS = [{"kind": "refresh", "row": 5, "entry": {
    "kode": "CS301", "tanggal": "06/09/2026", "pertemuan": "4", "dosen": "Dosen"}}]

state5, q5, mlog5 = run(lambda: _pick("rzkr:0", c1, REFRESH_ITEMS))
check("handler: reply diff + state ZOOM hidup",
      state5 == rekap.ZOOM and q5.answered
      and any(t.startswith("🔃 CS301 p.4: O 30→32, Q 18→24") for t in q5.message.replies),
      f"got state={state5} replies={q5.message.replies}")
check("handler: usage tercatat rekap-refresh",
      mlog5.call_args and mlog5.call_args[0][2] == "rekap-refresh" and mlog5.call_args[0][3] == "CS301",
      f"got {mlog5.call_args}")

state6, q6, _ = run(lambda: _pick("rzkr:0", c2, REFRESH_ITEMS))
check("handler: 0-diff -> '✅ Angka masih akurat'",
      state6 == rekap.ZOOM
      and any(t == "✅ Angka masih akurat — belum ada perubahan" for t in q6.message.replies),
      f"got replies={q6.message.replies}")

cfail = FakeRefreshClient({}, {}, row_with("30", "30", "18", "2", "8"), fail_counts=True)
state7, q7, _ = run(lambda: _pick("rzkr:0", cfail, REFRESH_ITEMS))
check("handler: fail-open SheetsError -> ⚠️ + picker tetap hidup",
      state7 == rekap.ZOOM and any(t.startswith("⚠️ Gagal hitung ulang") for t in q7.message.replies),
      f"got replies={q7.message.replies}")

state8, q8, _ = run(lambda: _pick("rzkr:99", c1, REFRESH_ITEMS))
check("handler: indeks kedaluwarsa -> pesan + state ZOOM",
      state8 == rekap.ZOOM and any("kedaluwarsa" in t for t in q8.message.replies),
      f"got replies={q8.message.replies}")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)