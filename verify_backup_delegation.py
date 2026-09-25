"""Offline verify — 2 bugfix backup (no creds, gspread stubbed):

Bug 1 PPC01 transfer: kelas A yang punya baris Backup aktif (A = fasil awal,
match kode+tanggal minggu ini) hilang dari picker A (_fetch_classes), dan
record pengganti di Zoom Record dianggap DONE utk A (_get_done_by_date) tanpa
menulis paksa col C (attribusi tetap perekam).

Bug 2 week leak: _fetch_backup_classes membuang backup basi (tanggal di luar
minggu WIB berjalan) — 'Selasa 8 Sep' tidak boleh muncul di 'Selasa 15 Sep'.

Run: py verify_backup_delegation.py
"""
from __future__ import annotations

from pathlib import Path

import sheets
from config import Config

SS_MAIN = "ss_main"
PASS, FAIL = [], []


def check(label, cond, msg=""):
    (PASS if cond else FAIL).append((label, msg))
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))


class FakeWorksheet:
    def __init__(self, title, rows, row_count=1000, col_count=26):
        self.title, self.rows = title, rows
        self.row_count, self.col_count = row_count, col_count

    def get_all_values(self):
        return [list(r) for r in self.rows]


class FakeSpreadsheet:
    def __init__(self, ss_id, worksheets):
        self.id = ss_id
        self._ws = {w.title: w for w in worksheets}

    def worksheet(self, title):
        return self._ws[title]


class FakeClient:
    def __init__(self, spreadsheets):
        self.spreadsheets = spreadsheets

    def open_by_key(self, ss_id):
        return self.spreadsheets[ss_id]


def cfg_for() -> Config:
    return Config(
        bot_token="x", sheet_id=SS_MAIN, service_account_json=Path("secrets/none.json"),
        facilitator_name="", master_sheet="Master", zoom_record_sheet="Zoom Record",
        backup_sheet="Backup", cancel_sheet="Cancel", absen_sheet_id="ss_absen",
        absen_sheet_name="Absen", rekap_sheet_id="ss_rekap", rekap_bukti_folder_id="",
        semester="1", reminder_slots=((21, 0),), reminder_enabled=False,
        heartbeat_hour=22, heartbeat_minute=0, heartbeat_enabled=False,
    )


def fresh_client(ss_main):
    c = sheets.SheetsClient(cfg_for())
    c._gc = FakeClient({SS_MAIN: ss_main})
    sheets._rows_cache.clear()
    sheets._tabs_cache.clear()
    sheets._classes_cache.clear()
    return c


def _mon(offset_days: int = 0) -> str:
    """Tanggal minggu berjalan: Senin + offset hari -> 'Senin, 21 September 2026'."""
    mon, _ = sheets.week_span_wib()
    from datetime import timedelta
    d = mon + timedelta(days=offset_days)
    return f"{sheets.DAY_ORDER[d.weekday()]}, {d.day} {sheets.ID_MONTHS_INV[f'{d.month:02d}']} {d.year}"


def main() -> None:
    # Master cols: [0]Hari [1]Fasil [2]Kategori [3]Jam [4]Kode [5]MK [6]Dosen [8]RomBel [9]SKS [10]ZoomNo [11]Link
    def master_row(day, fasil, kode):
        r = [""] * 16
        r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11] = (
            day, fasil, "Reguler", "13.00 - 15.30", kode, "MK", "Dosen X", "R1", "3 Ilkom", "3", "28", "https://meet/x")
        return r

    # Backup cols: [1]FasilAwal(B) [2]HariTanggal(C) [4]Kode(E) [8]Pengganti(I)
    def backup_row(awal, hari, kode, pengganti):
        r = [""] * 10
        r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8] = (
            awal, hari, "13.00 - 15.30", kode, "MK", "Dosen X", "R1", pengganti)
        return r

    # Zoom Record cols: [2]C=who [3]D=lecture_date [5]F=kode
    def zoom_row(who, tgl, kode):
        r = [""] * 15
        r[2], r[3], r[5] = who, tgl, kode
        return r

    # ========== Bug 1: delegasi PPC01 dari A ke B ==========
    MASTER = [
        ["Header"] * 16,
        master_row("Senin", "Fasil A", "PPC01"),
        master_row("Selasa", "Fasil B", "MD02"),  # milik B — tak boleh kena drop A
    ]
    BACKUP = [
        ["info"],
        ["header"] * 10,
        backup_row("Fasil A", _mon(0), "PPC01", "Fasil B"),      # A delegasi PPC01 minggu ini
        backup_row("Fasil A", _mon(-14), "PPC01", "Fasil B"),    # basi 2 minggu lalu — bukan delegasi aktif
    ]
    ZOOM = [
        ["header"] * 15,
        zoom_row("Fasil B", _mon(0).split(", ")[1], "PPC01"),    # B catat (col C = B), tanggal Senin minggu ini
    ]

    ss = FakeSpreadsheet(SS_MAIN, [
        FakeWorksheet("Master", MASTER),
        FakeWorksheet("Backup", BACKUP),
        FakeWorksheet("Zoom Record", ZOOM),
    ])
    c = fresh_client(ss)

    # 1a. Picker A: PPC01 (delegasi aktif minggu ini) TIDAK tampil; MD02 milik B tak terpengaruh.
    pa = c._fetch_classes("Fasil A")
    check("1a. PPC01 A disembunyikan saat delegasi aktif (baris Backup match kode+tanggal)",
          all(e.code != "PPC01" for e in pa), f"got {[e.code for e in pa]}")
    pb = c._fetch_classes("Fasil B")
    check("1b. Kelas fasil lain tetap tampil utk pemiliknya",
          any(e.code == "MD02" for e in pb), f"got {[e.code for e in pb]}")

    # 1d. done-by-date A: record pengganti (col C=Fasil B) dihitung done utk A.
    #     (dihitung SEBELUM membuat ss2 — _rows_cache global ber-ss_id sama
    #      antar fixture, jadi jangan campur isi sheet antar skenario)
    done_a = c._get_done_by_date("Fasil A")
    mon_dmy = c._norm_date(_mon(0).split(", ")[1])  # '21 September 2026' -> '21/09/2026'
    check("1d. delegasi aktif = done utk A (record pengganti tercatat)",
          ("ppc01", mon_dmy) in done_a, f"got {sorted(done_a)}")

    # 1e. done-by-date A: attribusi record tetap perekam — col C Zoom Record utuh = Fasil B.
    rec_c = ZOOM[1][2]
    check("1e. col C Zoom Record tetap perekam (Fasil B), tidak dipaksa ke A",
          rec_c == "Fasil B", f"got {rec_c!r}")

    # 1f. done-by-date A: tidak menelan kelas milik B / kode lain.
    check("1f. done utk A hanya delegasi A (no false positive)",
          ("md02", mon_dmy) not in done_a and ("ppc01", "08/09/2026") not in done_a,
          f"got {sorted(done_a)}")

    # 1c. Setelah minggu berlalu (delegasi basi), PPC01 kembali ke picker A.
    BACKUP_ONLY_STALE = [
        BACKUP[0], BACKUP[1],
        backup_row("Fasil A", _mon(-14), "PPC01", "Fasil B"),
    ]
    ss2 = FakeSpreadsheet(SS_MAIN, [
        FakeWorksheet("Master", MASTER),
        FakeWorksheet("Backup", BACKUP_ONLY_STALE),
        FakeWorksheet("Zoom Record", ZOOM),
    ])
    c2 = fresh_client(ss2)
    pa2 = c2._fetch_classes("Fasil A")
    check("1c. PPC01 kembali ke picker A setelah minggu delegasi berlalu",
          any(e.code == "PPC01" for e in pa2), f"got {[e.code for e in pa2]}")

    # ========== Bug 2: backup basi lintas minggu dibuang ==========
    BACKUP_MIX = [
        ["info"],
        ["header"] * 10,
        backup_row("Fasil X", _mon(0), "BX01", "Fasil B"),    # Senin minggu ini -> tampil
        backup_row("Fasil X", _mon(1), "BX02", "Fasil B"),    # Selasa minggu ini -> tampil
        backup_row("Fasil X", _mon(-14), "OLD1", "Fasil B"),  # basi 2 minggu lalu -> buang
        backup_row("Fasil X", "Selasa, 8 September 2026", "OLD2", "Fasil B"),  # basi fixed -> buang
        backup_row("Fasil X", "goreng", "NOPARSE", "Fasil B"),  # tak ter-parse -> fail-open tampil
    ]
    ss3 = FakeSpreadsheet(SS_MAIN, [FakeWorksheet("Backup", BACKUP_MIX)])
    c3 = fresh_client(ss3)
    b3 = c3._fetch_backup_classes("Fasil B")
    codes3 = [e.code for e in b3]
    check("2a. backup minggu ini tetap tampil",
          "BX01" in codes3 and "BX02" in codes3, f"got {codes3}")
    check("2b. backup basi (2 minggu lalu / tanggal lewat) DIBUANG dari backup list",
          "OLD1" not in codes3 and "OLD2" not in codes3, f"got {codes3}")
    check("2c. backup tanggal tak ter-parse tetap tampil (fail-open)",
          "NOPARSE" in codes3, f"got {codes3}")

    # ========== Bug 2 (reminder level): backup basi tidak spam minggu berikutnya ==========
    import asyncio
    import json
    import tempfile
    from unittest.mock import patch

    from handlers import reminder
    import users as users_mod

    today = sheets.today_day_wib()
    BY_STALE = sheets.ClassEntry(
        code="OLD1", subject="MK", day=today, time_range="08.00 - 10.00",
        category="Backup", lecturer="D", room="R1", rombel="", sks="3",
        zoom_number="", zoom_link="", backup_hari_tanggal=_mon(-14))  # 2 minggu lalu
    BY_WEEK = sheets.ClassEntry(
        code="BX02", subject="MK", day=today, time_range="08.00 - 10.00",
        category="Backup", lecturer="D", room="R1", rombel="", sks="3",
        zoom_number="", zoom_link="", backup_hari_tanggal=_mon(0))  # minggu ini

    with patch.dict("os.environ", {}, clear=False):
        _tmp = tempfile.mkdtemp(prefix="verify_bk_")
        (Path(_tmp) / "users.json").write_text(json.dumps({"7": "Fasil B"}), encoding="utf-8")
        users_mod.init(Path(_tmp) / "users.json", default_name="")

        class _FC:
            async def get_all_loggable_classes(self, f):
                return ([], [BY_STALE, BY_WEEK], [])
            async def get_done_by_date(self, f):
                return set()

        class _Bot:
            def __init__(self):
                self.sent = []
            async def send_message(self, chat_id, text, parse_mode=None):
                self.sent.append((chat_id, text))
                return None

        class _Job:
            chat_id, name = 7, "reminder:7:2100"  # slot pagi (full) — scan penuh

        class _Ctx:
            def __init__(self):
                self.job, self.bot = _Job(), _Bot()
                self.bot_data = {"cfg": cfg_for(), "sheets": _FC()}

        ctx = _Ctx()
        try:
            asyncio.new_event_loop().run_until_complete(reminder.reminder_job(ctx))
        except Exception as exc:  # full-slot path berhenti bila tak ada mine
            pass
        # BY_STALE day = hari_day ini (day-name match) tapi 2 minggu lalu -> harus
        # TIDAK picu kiriman; BY_WEEK (hari sama, minggu ini) -> boleh kirim.
        sent_text = "\n".join(t for _, t in ctx.bot.sent)
        check("2d. reminder_job: backup basi (2 minggu lalu) TIDAK picu kiriman lintas minggu",
              "OLD1" not in sent_text, f"sent={len(ctx.bot.sent)} mine bermasalah")
        check("2e. reminder_job: backup minggu ini tetap diremind",
              "BX02" in sent_text, f"sent={len(ctx.bot.sent)}")

        print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
        for lbl, msg in FAIL:
            print(f"  FAILED: {lbl} {msg}")
        raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()