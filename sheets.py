"""Google Sheets access via gspread.

gspread is synchronous — every public function here is an async wrapper that
runs the blocking call in the default executor (loop.run_in_executor).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial

import gspread
import gspread.exceptions
from google.oauth2.service_account import Credentials

from config import Config

log = logging.getLogger(__name__)

_absen_kodes_cache: dict = {"data": None, "ts": 0}
_absen_students_cache: dict = {}  # kode -> (ts, [(nim, nama)]); TTL see _ABSEN_STUDENTS_TTL
_ABSEN_STUDENTS_TTL = 300
_classes_cache: dict = {}
_rekap_status_cache: dict = {}
_rekap_tab_cache: dict = {}
_CACHE_TTL = 120
_feedback_cache: dict = {"rows": None, "ts": 0}
# Whole-sheet row caches: (ss_id, title) -> (ts, rows). Mutated only inside
# _run()'s single-flight lock, so no extra synchronization needed.
_rows_cache: dict[tuple[str, str], tuple[float, list[list[str]]]] = {}
_tabs_cache: dict[str, tuple[float, list[str]]] = {}
_ROWS_TTL = 300
SCHOOL_KEYWORDS = {
    "school of ai & computer science": ["computer science", "artificial intelligence", "informatics", "information system", "data science", "ai &"],
    "school of engineering": ["industrial engineering", "electrical engineering", "engineering"],
    "school of business economics": ["digital business", "finance", "accounting", "business management", "business", "management"],
    "school of communication & design": ["visual communication", "communication", "journalism", "broadcast", "design"],
    "school of psychology & education": ["psycholog", "education"],
    "school of law": ["law"],
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

WIB = timezone(timedelta(hours=7), name="WIB")

# Master schedule columns (0-indexed) — Batch 5 Ganjil 26/27
# Header: [0]Hari [1]NamaFasil [2]Kategori [3]Jam [4]Kode [5]MK [6]Dosen [7]Ruang [8]RomBel [9]SKS [10]ZoomNo [11]ZoomLink [12]Keterangan [13]KetuaKelas [14]HPKetua
COL_DAY, COL_FASIL, COL_KATEGORI, COL_JAM, COL_KODE = 0, 1, 2, 3, 4
COL_MK, COL_DOSEN, COL_RUANG, COL_ROMBEL, COL_SKS, COL_ZOOM_NO, COL_ZOOM_LINK, COL_KETERANGAN = 5, 6, 7, 8, 9, 10, 11, 12

TIPE_KELAS_MAP = {"reguler": "Reguler", "professional": "Professional", "akselerasi": "Akselerasi", "akselerasi & professional": "Akselerasi & Professional", "professional & akselerasi": "Akselerasi & Professional", "pro": "Professional", "ae": "Akselerasi", "ae & pro": "Akselerasi & Professional", "pro & ae": "Akselerasi & Professional"}
DAY_ORDER = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


class SheetsError(RuntimeError):
    """User-presentable Sheets failure."""


@dataclass
class ClassEntry:
    """One row from the master facilitator sheet."""

    code: str
    subject: str
    day: str            # e.g. "Senin"
    time_range: str     # e.g. "13.00 - 15.30"
    category: str       # Reguler / Professional / Akselerasi / Backup
    lecturer: str
    room: str
    rombel: str
    sks: str
    zoom_number: str
    zoom_link: str
    keterangan: str = ""
    backup_hari_tanggal: str = ""  # only for backup classes: full "Senin, 8 September 2026"
    semester: str = ""  # derived from RomBel e.g. "3 & 4"
    row_index: int = -1  # 0-based index in get_all_values()

    @property
    def start_time(self) -> str:
        """'13.00 - 15.30' -> '13.00' (col M of Zoom Record)."""
        m = re.match(r"\s*([0-2]?\d[.:]\d{2})", self.time_range)
        return m.group(1) if m else self.time_range.strip()

    @property
    def tipe_kelas(self) -> str:
        key = self.category.strip().lower()
        return TIPE_KELAS_MAP.get(key, self.category.strip() or "Regular")

    @property
    def zoom_label(self) -> str:
        """Priority: Keterangan Zoom number > Nomor Zoom. Link di Keterangan diabaikan — kosongkan."""
        if self.keterangan:
            m = re.search(r"Zoom\s*(\d+)", self.keterangan, re.IGNORECASE)
            if m:
                return f"Zoom {m.group(1)}"
        num = self.zoom_number.strip()
        if num:
            return f"Zoom {num}"
        return ""


@dataclass
class LogRecord:
    """A row to append to the Zoom Record sheet (columns B..O)."""

    facilitator: str
    lecture_date: str      # display string, e.g. "06/09/2026"
    entry_date: str
    semester: str
    subject: str
    code: str
    meeting: str
    scheme: str            # Online / Offline
    sks: str
    tipe_kelas: str
    lecturer: str
    start_time: str
    zoom: str
    notes: str

    def as_row(self) -> list[str]:
        """Columns B..O in order (A is prepended as blank at append site)."""
        return [
            self.entry_date,      # B
            self.facilitator,     # C
            self.lecture_date,    # D
            self.semester,        # E
            self.code,            # F  (Kelas)
            self.subject,         # G  (Mata Kuliah)
            self.meeting,         # H
            self.scheme,          # I
            self.sks,             # J
            self.tipe_kelas,      # K
            self.lecturer,        # L
            self.start_time,      # M
            self.zoom,            # N
            self.notes,           # O
        ]


@dataclass
class BackupRecord:
    """A row for Backup Fasilitator sheet (B-J)."""
    facilitator_awal: str
    hari_tanggal: str
    jam: str
    kode: str
    subject: str
    lecturer: str
    room: str
    pengganti: str
    catatan: str

    def as_row(self) -> list[str]:
        return [
            self.facilitator_awal,  # B
            self.hari_tanggal,      # C
            self.jam,               # D
            self.kode,              # E
            self.subject,           # F
            self.lecturer,          # G
            self.room,              # H
            self.pengganti,         # I
            self.catatan,           # J
        ]


@dataclass
class CancelRecord:
    """A row for Kelas Cancel & Pengganti sheet (B-I)."""
    lecturer: str
    subject: str
    jadwal_awal: str
    jam: str
    sesi: str
    kode: str
    sks: str
    facilitator: str

    def as_row(self) -> list[str]:
        return [
            self.lecturer,     # B
            self.subject,      # C
            self.jadwal_awal,  # D
            self.jam,          # E
            self.sesi,         # F
            self.kode,         # G
            self.sks,          # H
            self.facilitator,  # I
        ]


ID_MONTHS = {"januari": "01", "februari": "02", "maret": "03", "april": "04",
             "mei": "05", "juni": "06", "juli": "07", "agustus": "08",
             "september": "09", "oktober": "10", "november": "11", "desember": "12"}
ID_MONTHS_INV = {v: k.capitalize() for k, v in ID_MONTHS.items()}


def tanggal_panjang(ddmmyyyy: str) -> str:
    """'08/09/2026' -> '8 September 2026' (format nama file & kolom Tanggal Kehadiran)."""
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", ddmmyyyy.strip())
    if not m:
        return ddmmyyyy
    return f"{int(m.group(1))} {ID_MONTHS_INV.get(m.group(2).zfill(2), m.group(2))} {m.group(3)}"


@dataclass
class RekapRecord:
    """A row for Rekap Kehadiran sheet (B-W). O-W auto from absen if available."""

    facilitator: str       # full registered name (for filename)
    tanggal: str           # "7 September 2026" (col B)
    lecturer: str          # C
    jam: str               # D (time_range)
    kode: str              # E
    subject: str           # F
    sks: str               # G
    pertemuan: str         # H
    tipe: str              # I (Online / On-site)
    sesi: str              # J (Kelas Biasa)
    peran: str             # K (Fasilitator Kelas / Fasilitator Pengganti)
    bukti: str             # L (Drive link, may be "")
    bukti_name: str = ""   # display filename for HYPERLINK
    total: str = ""        # O
    hadir: str = ""        # P
    feedback: str = ""     # Q
    tidak: str = ""        # R
    belum: str = ""        # S

    def as_row(self) -> list[str]:
        # B..W: B,C,D,E,F,G,H,I,J,K,L,M,N,O,P,Q,R,S,T,U,V,W
        return [
            self.tanggal, self.lecturer, self.jam, self.kode, self.subject,
            self.sks, self.pertemuan, self.tipe, self.sesi, self.peran,
            self.bukti, "", "",
            self.total, self.hadir, self.feedback, self.tidak, self.belum,
            "", "", "", "",
        ]

    def bukti_filename(self, ext: str = "jpg") -> str:
        first = self.facilitator  # full name per user decision
        return f"{self.tanggal}_{self.lecturer}_{self.subject}_{first}.{ext}"


def today_str_wib() -> str:
    return datetime.now(WIB).strftime("%d/%m/%Y")


def next_date_for_day(day_name: str) -> str:
    """Return dd/mm/yyyy for next occurrence of Indonesian day name (including today if matches)."""
    norm = re.sub(r"[^a-z]", "", day_name.strip().lower())
    target = next((i for i, d in enumerate(DAY_ORDER) if re.sub(r"[^a-z]", "", d.lower()) == norm), None)
    if target is None:
        return today_str_wib()
    today = datetime.now(WIB)
    delta = (target - today.weekday()) % 7
    return (today + timedelta(days=delta)).strftime("%d/%m/%Y")


def last_date_for_day(day_name: str) -> str:
    """Return dd/mm/yyyy for most recent occurrence of day (including today)."""
    norm = re.sub(r"[^a-z]", "", day_name.strip().lower())
    target = next((i for i, d in enumerate(DAY_ORDER) if re.sub(r"[^a-z]", "", d.lower()) == norm), None)
    if target is None:
        return today_str_wib()
    today = datetime.now(WIB)
    delta = (today.weekday() - target) % 7
    return (today - timedelta(days=delta)).strftime("%d/%m/%Y")


def _mode_incompatible(mode: str, status: str) -> bool:
    """Warn when an absen status contradicts the student's col-C Mode Kelas Asal
    (e.g. Online marked as on-site 'S'). Soft check — never blocks the write."""
    m = (mode or "").strip().casefold()
    s = (status or "").strip().upper()
    online = "online" in m
    onsite = any(k in m for k in ("onsite", "offline", "tatap", "ceramah"))
    if not (online or onsite):
        return False
    if online and not onsite and s in ("S", "SF"):
        return True
    if onsite and not online and s in ("O", "OF"):
        return True
    return False


class SheetsClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._gc: gspread.Client | None = None
        self._lock = asyncio.Lock()  # single-flight for ALL gspread I/O
        self._chat_locks: dict[int, asyncio.Lock] = {}

    # ---------- sync internals ----------

    def _client(self) -> gspread.Client:
        if self._gc is None:
            creds = Credentials.from_service_account_file(str(self.cfg.service_account_json), scopes=SCOPES)
            self._gc = gspread.authorize(creds)
        return self._gc

    def _ss(self, ss_id: str) -> gspread.Spreadsheet:
        try:
            return self._client().open_by_key(ss_id)
        except gspread.exceptions.APIError as exc:
            raise SheetsError(f"Google API error: {exc.response.status_code}") from exc

    def _sheet_in(self, ss_id: str, title: str) -> gspread.Worksheet:
        try:
            return self._ss(ss_id).worksheet(title)
        except gspread.exceptions.WorksheetNotFound as exc:
            raise SheetsError(f"Sheet '{title}' tidak ditemukan — cek nama tab / config.") from exc

    def _cached_rows(self, ss_id: str, title: str) -> list[list[str]]:
        """Whole-sheet get_all_values with TTL. Safe: only called under _run single-flight."""
        key = (ss_id, title)
        hit = _rows_cache.get(key)
        if hit and time.time() - hit[0] < _ROWS_TTL:
            return hit[1]
        rows = self._sheet_in(ss_id, title).get_all_values()
        _rows_cache[key] = (time.time(), rows)
        return rows

    def _invalidate_rows(self, ss_id: str, title: str | None = None) -> None:
        """Write-through: drop cached rows after a successful write."""
        for k in [k for k in _rows_cache if k[0] == ss_id and (title is None or k[1] == title)]:
            _rows_cache.pop(k, None)

    def _tabs(self, ss_id: str) -> list[str]:
        """Worksheet titles of a spreadsheet (cached 10 min)."""
        hit = _tabs_cache.get(ss_id)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        titles = [w.title for w in self._ss(ss_id).worksheets()]
        _tabs_cache[ss_id] = (time.time(), titles)
        return titles

    @asynccontextmanager
    async def for_chat(self, chat_id: int):
        """Per-chat async lock — serializes a chat's gspread-write handlers under
        concurrent_updates=True so double-taps can't interleave or double-write."""
        lock = self._chat_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            yield

    def _sheet(self, title: str) -> gspread.Worksheet:
        return self._sheet_in(self.cfg.sheet_id, title)

    def _normalize(self, v: str) -> str:
        return re.sub(r"\s+", " ", (v or "").strip()).casefold()

    def _fetch_classes(self, facilitator_name: str) -> list[ClassEntry]:
        rows = self._cached_rows(self.cfg.sheet_id, self.cfg.master_sheet)
        target = self._normalize(facilitator_name)
        out: list[ClassEntry] = []
        for i, row in enumerate(rows):
            if len(row) <= COL_ZOOM_LINK or i == 0:
                continue  # skip short rows + header
            sheet_name = self._normalize(row[COL_FASIL])
            # Partial match: "Ratu" matches "Ratu Bilqis"
            if target not in sheet_name:
                continue
            code = row[COL_KODE].strip()
            if not code:
                continue
            # Derive semester from RomBel e.g. "3 Ilkom Pro, 4 Ilkom Pro" -> "3 & 4"
            rombel_raw = row[COL_ROMBEL].strip()
            sems = sorted(set(re.findall(r"\b(\d+)\b", rombel_raw)))
            semester = " & ".join(sems) if sems else ""
            out.append(
                ClassEntry(
                    code=code,
                    subject=row[COL_MK].strip(),
                    day=row[COL_DAY].strip(),
                    time_range=row[COL_JAM].strip(),
                    category=row[COL_KATEGORI].strip(),
                    lecturer=row[COL_DOSEN].strip(),
                    room=rombel_raw,
                    rombel=rombel_raw,
                    sks=row[COL_SKS].strip(),
                    zoom_number=row[COL_ZOOM_NO].strip(),
                    zoom_link=row[COL_ZOOM_LINK].strip(),
                    keterangan=row[COL_KETERANGAN].strip() if len(row) > COL_KETERANGAN else "",
                    semester=semester,
                    row_index=i,
                )
            )
        log.info("Fetched %d classes for %s", len(out), facilitator_name)
        return out

    # Columns we write to (B,C,D,E,F,H,I,M,N,O)
    # Skip G,J,K,L — those have formulas pulling from MatkulMaster sheet
    WRITE_COLS = [0, 1, 2, 3, 4, 6, 7, 11, 12, 13]  # as_row indices

    def _append_record(self, rec: LogRecord) -> int:
        ss = self._ss(self.cfg.sheet_id)
        ws = ss.worksheet(self.cfg.zoom_record_sheet)
        row_data = rec.as_row()
        # Find first truly empty row: ALL columns B-O must be empty
        all_vals = self._cached_rows(self.cfg.sheet_id, self.cfg.zoom_record_sheet)
        insert_row = len(all_vals) + 1  # default: append at end
        for i, r in enumerate(all_vals):
            if i == 0:
                continue  # skip header
            # Check if ANY column B-O (indices 1-14) has data
            has_data = False
            for ci in range(1, 15):  # B=1 to O=14
                if ci < len(r) and r[ci].strip():
                    has_data = True
                    break
            if not has_data:
                insert_row = i + 1  # 1-indexed
                break
        # Write only to unprotected columns: B,C,D,E,F,H,I,M,N,O in ONE batch call
        # Map: as_row index -> column letter
        unprotected_map = {
            0: "B", 1: "C", 2: "D", 3: "E", 4: "F",
            6: "H", 7: "I", 11: "M", 12: "N", 13: "O",
        }
        data = [{"range": f"{col}{insert_row}", "values": [[row_data[idx]]]}
                for idx, col in unprotected_map.items()]
        ss.values_batch_update({"valueInputOption": "USER_ENTERED", "data": data})
        self._invalidate_rows(self.cfg.sheet_id, self.cfg.zoom_record_sheet)
        log.info("Wrote record %s/%s at row %d (unprotected cols only)", rec.code, rec.meeting, insert_row)
        return ws.row_count

    # ---------- async API ----------

    async def get_classes(self, facilitator_name: str | None = None) -> list[ClassEntry]:
        # Empty guard: normalized "" would false-match master-sheet rows with blank fasilitator cells.
        name = (facilitator_name or self.cfg.facilitator_name).strip()
        if not name:
            raise SheetsError("Nama fasilitator kosong — ketik /register <nama> dulu.")
        import time as _t
        key = self._normalize(name)
        hit = _classes_cache.get(key)
        if hit and _t.time() - hit[0] < _CACHE_TTL:
            return hit[1]
        res = await self._run(partial(self._fetch_classes, name))
        _classes_cache[key] = (_t.time(), res)
        return res

    def _find_facilitator_names(self, search: str) -> list[str]:
        """Find unique full facilitator names matching search term, with alias support."""
        # Check aliases first
        try:
            import json
            from pathlib import Path
            alias_path = Path(__file__).parent / "data" / "aliases.json"
            if alias_path.exists():
                aliases = json.loads(alias_path.read_text(encoding="utf-8"))
                key = self._normalize(search)
                if key in {self._normalize(k): v for k, v in aliases.items()}:
                    # Find matching alias (case-insensitive)
                    for k, v in aliases.items():
                        if self._normalize(k) == key:
                            return [v]
        except Exception as exc:
            log.warning("alias lookup failed: %s", exc)
        rows = self._cached_rows(self.cfg.sheet_id, self.cfg.master_sheet)
        target = self._normalize(search)
        found = set()
        for i, row in enumerate(rows):
            if i == 0 or len(row) <= COL_FASIL:
                continue
            sheet_name = row[COL_FASIL].strip()
            if sheet_name and target in self._normalize(sheet_name):
                found.add(sheet_name)
        return sorted(found)

    async def find_facilitator_names(self, search: str) -> list[str]:
        return await self._run(partial(self._find_facilitator_names, search))

    def _get_next_meeting(self, kode: str) -> tuple[str, str, str]:
        """Return (last_meeting_str, next_single, next_double). Scans Zoom Record col F=Kode, H=Pertemuan."""
        try:
            rows = self._cached_rows(self.cfg.sheet_id, self.cfg.zoom_record_sheet)
        except Exception as exc:
            log.warning("next-meeting scan failed: %s", exc)
            return ("", "1", "1 dan 2")
        import re
        max_n = 0
        last_str = ""
        for r in rows[1:]:
            if len(r) <= 7: continue
            if r[5].strip().casefold() != kode.strip().casefold():
                continue
            pert = r[7].strip()
            if not pert: continue
            last_str = pert
            nums = [int(n) for n in re.findall(r"\d+", pert)]
            if nums:
                max_n = max(max_n, max(nums))
        if max_n == 0:
            return ("", "1", "1 dan 2")
        nxt = max_n + 1
        return (last_str, str(nxt), f"{nxt} dan {nxt+1}")

    async def get_next_meeting(self, kode: str) -> tuple[str, str, str]:
        return await self._run(partial(self._get_next_meeting, kode))

    def _get_done_map(self, facilitator_name: str) -> set[str]:
        """Set of kode already in Zoom Record for facilitator (any pertemuan) — for backward compat."""
        try:
            rows = self._cached_rows(self.cfg.sheet_id, self.cfg.zoom_record_sheet)
        except Exception as exc:
            log.warning("done-map scan failed: %s", exc)
            return set()
        target = self._normalize(facilitator_name)
        done = set()
        for r in rows[1:]:
            if len(r) <= 5: continue
            if self._normalize(r[2]) != target:
                continue
            kode = r[5].strip().casefold()
            if kode:
                done.add(kode)
        return done

    async def get_done_map(self, facilitator_name: str) -> set[str]:
        return await self._run(partial(self._get_done_map, facilitator_name))

    def _norm_date(self, s: str) -> str:
        """Normalize 'Senin, 8 September 2026' or '08/09/2026' -> '08/09/2026'."""
        import re
        s = s.strip()
        # Already dd/mm/yyyy
        if re.match(r"\d{2}/\d{2}/\d{4}", s):
            return s
        m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", s)
        if m:
            d, mon, y = m.group(1), m.group(2).lower(), m.group(3)
            months = {"januari":"01","februari":"02","maret":"03","april":"04","mei":"05","juni":"06","juli":"07","agustus":"08","september":"09","oktober":"10","november":"11","desember":"12"}
            mm = months.get(mon, "01")
            return f"{int(d):02d}/{mm}/{y}"
        return s

    def _get_done_by_date(self, facilitator_name: str) -> set[tuple[str, str]]:
        """Set of (kode, tanggal_kelas) already in Zoom Record — for daily check (gabung: 1 entry cover 2 sesi)."""
        try:
            rows = self._cached_rows(self.cfg.sheet_id, self.cfg.zoom_record_sheet)
        except Exception as exc:
            log.warning("done-by-date scan failed: %s", exc)
            return set()
        target = self._normalize(facilitator_name)
        done = set()
        for r in rows[1:]:
            if len(r) <= 5: continue
            if self._normalize(r[2]) != target:
                continue
            kode = r[5].strip().casefold()
            tgl = self._norm_date(r[3].strip())  # Col D
            if kode and tgl:
                done.add((kode, tgl))
        return done

    async def get_done_by_date(self, facilitator_name: str) -> set[tuple[str, str]]:
        return await self._run(partial(self._get_done_by_date, facilitator_name))

    def _all_absen_rows(self) -> dict[str, list[list[str]]]:
        """All absen worksheet rows {title: rows} in ONE values.batchGet, cached per title."""
        titles = self._tabs(self.cfg.absen_sheet_id)
        now = time.time()
        need = [t for t in titles
                if not _rows_cache.get((self.cfg.absen_sheet_id, t))
                or now - _rows_cache[(self.cfg.absen_sheet_id, t)][0] >= _ROWS_TTL]
        if need:
            resp = self._ss(self.cfg.absen_sheet_id).values_batch_get([f"'{t}'" for t in need])
            for t, vr in zip(need, resp.get("valueRanges", []) or []):
                _rows_cache[(self.cfg.absen_sheet_id, t)] = (now, vr.get("values", []) or [])
        return {t: _rows_cache[(self.cfg.absen_sheet_id, t)][1] for t in titles}

    def _list_absen_kodes(self) -> list[str]:
        import time
        now = time.time()
        if _absen_kodes_cache["data"] and now - _absen_kodes_cache["ts"] < 300:
            return _absen_kodes_cache["data"]
        kodes = []
        for rows in self._all_absen_rows().values():
            for r in rows:
                if r[0].strip() == "Kode Kelas" and len(r) > 1 and r[1].strip():
                    kodes.append(r[1].strip())
        res = sorted(set(kodes))
        _absen_kodes_cache["data"] = res
        _absen_kodes_cache["ts"] = now
        return res

    async def list_absen_kodes(self) -> list[str]:
        return await self._run(self._list_absen_kodes)

    def _list_students(self, kode: str) -> list[tuple[str, str, str]]:
        """Roster (nim, nama, mode) for a Kode, aggregated across ALL matching
        absen blocks (a class may span several prodi tabs)."""
        key = kode.casefold()
        hit = _absen_students_cache.get(key)
        if hit and time.time() - hit[0] < _ABSEN_STUDENTS_TTL:
            return hit[1]
        try:
            blocks = self._locate_absen_block(kode)
        except SheetsError:
            _absen_students_cache[key] = (time.time(), [])
            return []
        out = []
        seen = set()
        for _, rows, nim_header in blocks:
            for r_idx in range(nim_header+2, len(rows)):
                if rows[r_idx][0].strip() == "Program Studi":
                    break
                nim = rows[r_idx][0].strip() if len(rows[r_idx])>0 else ""
                nama = rows[r_idx][1].strip() if len(rows[r_idx])>1 else ""
                mode = rows[r_idx][2].strip() if len(rows[r_idx])>2 else ""
                if not (nim or nama):
                    continue
                dkey = (nim.casefold(), nama.casefold())
                if dkey in seen:
                    continue
                seen.add(dkey)
                out.append((nim, nama or "-", mode))
        _absen_students_cache[key] = (time.time(), out)
        return out

    def _invalidate_absen_students(self, kode: str | None = None) -> None:
        """Dropped on absen writes so edits (roster / status) re-fetch. Empty result
        exposed via TTL only — never cached permanently."""
        if kode is None:
            _absen_students_cache.clear()
        else:
            _absen_students_cache.pop(kode.casefold(), None)

    async def list_students(self, kode: str) -> list[tuple[str, str, str]]:
        return await self._run(partial(self._list_students, kode))

    def _locate_absen_block(self, kode: str) -> list[tuple[str, list[list[str]], int]]:
        """Return ALL absen blocks (title, rows, nim_header) matching a Kode across
        every prodi sheet — a class can have several blocks (multi-prodi), so we
        never stop at the first match."""
        key = kode.casefold()
        found = []
        for title, rows in self._all_absen_rows().items():
            for i, r in enumerate(rows):
                if r[0].strip() == "Kode Kelas" and len(r) > 1 and r[1].strip().casefold() == key:
                    nim_header = -1
                    for j in range(i, min(i + 10, len(rows))):
                        if rows[j][0].strip() == "NIM":
                            nim_header = j
                            break
                    if nim_header == -1:
                        raise SheetsError("Header NIM tidak ditemukan")
                    found.append((title, rows, nim_header))
        if not found:
            raise SheetsError(f"Kode {kode} tidak ditemukan di sheet Absen (cek 15 prodi).")
        return found

    def _absen_counts(self, kode: str, pertemuan: int) -> dict:
        """Count statuses in a pertemuan column, aggregated across ALL matching
        absen blocks. P=S+O+SF+OF, Q=S+O, R=A, S_blm=SF+OF."""
        blocks = self._locate_absen_block(kode)
        if not 1 <= pertemuan <= 16:
            raise SheetsError("Pertemuan harus 1-16")
        col_idx = 3 + (pertemuan - 1)
        total = hadir = feedback = tidak = belum = izin = 0
        for _, rows, nim_header in blocks:
            for r_idx in range(nim_header + 2, len(rows)):
                if rows[r_idx][0].strip() == "Program Studi":
                    break
                nim = rows[r_idx][0].strip() if len(rows[r_idx]) > 0 else ""
                if not nim:
                    continue
                total += 1
                val = rows[r_idx][col_idx].strip().upper() if len(rows[r_idx]) > col_idx else ""
                if val in ("S", "O", "SF", "OF"):
                    hadir += 1
                if val in ("S", "O"):
                    feedback += 1
                if val == "A":
                    tidak += 1
                if val in ("SF", "OF"):
                    belum += 1
                if val == "I":
                    izin += 1
        sheets_list = sorted({b[0] for b in blocks})
        return {"total": total, "hadir": hadir, "feedback": feedback,
                "tidak": tidak, "belum": belum, "izin": izin,
                "sheet": sheets_list[0] if sheets_list else "",
                "prodi": sheets_list[0] if sheets_list else "",
                "sheets": sheets_list}

    async def absen_counts(self, kode: str, pertemuan: int) -> dict:
        return await self._run(partial(self._absen_counts, kode, pertemuan))

    def _resolve_absen(self, kode: str, identifiers: list[str]) -> dict:
        """Resolve identifiers to student rows across ALL matching absen blocks.
        NIM wajib exact-penuh, Nama contains.
        Returns {blocks, matched: [(row_idx, nim, nama, mode, title)],
        ambiguous: {ident: [nama]}, unmatched: [ident]}."""
        blocks = self._locate_absen_block(kode)
        roster = []
        for title, rows, nim_header in blocks:
            for r_idx in range(nim_header + 2, len(rows)):
                if rows[r_idx][0].strip() == "Program Studi":
                    break
                nim = rows[r_idx][0].strip() if len(rows[r_idx]) > 0 else ""
                nama = rows[r_idx][1].strip() if len(rows[r_idx]) > 1 else ""
                mode = rows[r_idx][2].strip() if len(rows[r_idx]) > 2 else ""
                if nim or nama:
                    roster.append((r_idx, nim, nama or "-", mode, title))
        matched, ambiguous, unmatched = [], {}, []
        for ident in identifiers:
            norm = self._normalize(ident)
            if not norm:
                continue
            if norm.isdigit():
                hits = [e for e in roster if e[1].strip() == norm]
            else:
                hits = [e for e in roster if norm in self._normalize(e[2])]
            if len(hits) == 1:
                if hits[0] not in matched:
                    matched.append(hits[0])
            elif len(hits) > 1:
                ambiguous[ident] = [e[2] for e in hits[:5]]
            else:
                unmatched.append(ident)
        return {"blocks": blocks, "matched": matched,
                "ambiguous": ambiguous, "unmatched": unmatched,
                "title": blocks[0][0] if blocks else "",
                "rows": blocks[0][1] if blocks else [],
                "nim_header": blocks[0][2] if blocks else -1}

    def _update_absen(self, kode: str, pertemuan: int, identifiers: list[str], status: str) -> dict:
        res = self._resolve_absen(kode, identifiers)
        if not 1 <= pertemuan <= 16:
            raise SheetsError("Pertemuan harus 1-16")
        col_idx = 3 + (pertemuan - 1)
        col_letter = chr(ord('A') + col_idx)
        updated = 0
        data = []
        written_titles = set()
        warnings = []
        for r_idx, nim, nama, mode, title in res["matched"]:
            data.append({"range": f"'{title}'!{col_letter}{r_idx+1}", "values": [[status]]})
            written_titles.add(title)
            updated += 1
            if mode and _mode_incompatible(mode, status):
                warnings.append(f"{nim or nama} ({mode})")
        if data:
            self._ss(self.cfg.absen_sheet_id).values_batch_update(
                {"valueInputOption": "USER_ENTERED", "data": data})
            for title in written_titles:
                self._invalidate_rows(self.cfg.absen_sheet_id, title)
        self._invalidate_absen_students(kode)
        log.info("Updated absen %s pertemuan %d status %s: %d rows (%s)",
                 kode, pertemuan, status, updated, ",".join(sorted(written_titles)) or "-")
        if updated == 0 and not res["ambiguous"] and not res["unmatched"]:
            raise SheetsError("Tidak ada NIM/Nama yang cocok")
        return {"updated": updated, "ambiguous": res["ambiguous"],
                "unmatched": res["unmatched"],
                "names": [nm for _, _, nm, _, _ in res["matched"][:10]],
                "sheets": sorted(written_titles),
                "sheet": res["title"],
                "warnings": warnings}

    async def update_absen(self, kode: str, pertemuan: int, identifiers: list[str], status: str) -> dict:
        return await self._run(partial(self._update_absen, kode, pertemuan, identifiers, status))

    def _fetch_backup_classes(self, facilitator_name: str) -> list[ClassEntry]:
        """Classes where facilitator is listed as Fasil Pengganti in Backup sheet."""
        try:
            rows = self._cached_rows(self.cfg.sheet_id, self.cfg.backup_sheet)
        except SheetsError:
            return []
        target = self._normalize(facilitator_name)
        out: list[ClassEntry] = []
        # Backup sheet: row0 = info, row1 = header, data from row2
        for i, row in enumerate(rows):
            if i < 2 or len(row) <= 8:
                continue
            pengganti = row[8].strip() if len(row) > 8 else ""  # Col I
            if not pengganti or target not in self._normalize(pengganti):
                continue
            kode = row[4].strip() if len(row) > 4 else ""  # Col E
            if not kode:
                continue
            hari_tanggal = row[2].strip() if len(row) > 2 else ""  # Col C
            day = hari_tanggal.split(",")[0].strip() if "," in hari_tanggal else hari_tanggal.split()[0] if hari_tanggal else ""
            # Lookup zoom/sks/semester from master by kode for backup class
            zoom_no = ""
            sks = ""
            semester = ""
            try:
                mrows = self._cached_rows(self.cfg.sheet_id, self.cfg.master_sheet)
                for mr in mrows:
                    if len(mr) > COL_KODE and mr[COL_KODE].strip().casefold() == kode.casefold():
                        zoom_no = mr[COL_ZOOM_NO].strip() if len(mr) > COL_ZOOM_NO else ""
                        sks = mr[COL_SKS].strip() if len(mr) > COL_SKS else ""
                        rombel_tmp = mr[COL_ROMBEL].strip() if len(mr) > COL_ROMBEL else ""
                        sems = sorted(set(re.findall(r"\b(\d+)\b", rombel_tmp)))
                        semester = " & ".join(sems) if sems else ""
                        break
            except Exception as exc:
                log.warning("backup master lookup failed: %s", exc)
            out.append(ClassEntry(
                code=kode,
                subject=row[5].strip() if len(row) > 5 else "",
                day=day or "Senin",
                time_range=row[3].strip() if len(row) > 3 else "",
                category="Backup",
                lecturer=row[6].strip() if len(row) > 6 else "",
                room=row[7].strip() if len(row) > 7 else "",
                rombel="",
                sks=sks,
                zoom_number=zoom_no,
                zoom_link="",
                keterangan=row[9].strip() if len(row) > 9 else "",
                backup_hari_tanggal=hari_tanggal,
                semester=semester,
                row_index=i,
            ))
        log.info("Fetched %d backup classes for %s", len(out), facilitator_name)
        return out

    async def get_backup_classes(self, facilitator_name: str) -> list[ClassEntry]:
        return await self._run(partial(self._fetch_backup_classes, facilitator_name))

    async def get_all_loggable_classes(self, facilitator_name: str) -> tuple[list[ClassEntry], list[ClassEntry]]:
        """Return (personal classes, backup classes)."""
        personal = await self.get_classes(facilitator_name)
        backup = await self.get_backup_classes(facilitator_name)
        return personal, backup

    def _append_backup_record(self, rec: BackupRecord) -> int:
        ws = self._sheet(self.cfg.backup_sheet)
        row_data = rec.as_row()
        all_vals = self._cached_rows(self.cfg.sheet_id, self.cfg.backup_sheet)
        insert_row = len(all_vals) + 1
        for i, r in enumerate(all_vals):
            if i < 2:
                continue  # skip 2 header rows
            has_data = any(c.strip() for c in r[1:10]) if len(r) > 1 else False
            if not has_data:
                insert_row = i + 1
                break
        ws.update(f"B{insert_row}:J{insert_row}", [row_data], value_input_option="USER_ENTERED")
        self._invalidate_rows(self.cfg.sheet_id, self.cfg.backup_sheet)
        log.info("Wrote backup %s/%s at row %d", rec.kode, rec.hari_tanggal, insert_row)
        return ws.row_count

    def _append_cancel_record(self, rec: CancelRecord) -> int:
        ws = self._sheet(self.cfg.cancel_sheet)
        row_data = rec.as_row()
        all_vals = self._cached_rows(self.cfg.sheet_id, self.cfg.cancel_sheet)
        insert_row = len(all_vals) + 1
        for i, r in enumerate(all_vals):
            if i == 0:
                continue
            has_data = any(c.strip() for c in r[1:9]) if len(r) > 1 else False
            if not has_data:
                insert_row = i + 1
                break
        ws.update(f"B{insert_row}:I{insert_row}", [row_data], value_input_option="USER_ENTERED")
        self._invalidate_rows(self.cfg.sheet_id, self.cfg.cancel_sheet)
        log.info("Wrote cancel %s/%s at row %d", rec.kode, rec.sesi, insert_row)
        return ws.row_count

    def _find_rekap_tab(self, facilitator_name: str) -> str:
        """Match registered name to per-fasil tab (nickname titles). Longest match wins."""
        target = self._normalize(facilitator_name)
        best, best_len = None, 0
        for t in self._tabs(self.cfg.rekap_sheet_id):
            t = t.strip()
            if t in ("PENTING DIBACA", "Template"):
                continue
            tn = self._normalize(t)
            if tn and tn in target and len(tn) > best_len:
                best, best_len = t, len(tn)
        if not best:
            raise SheetsError(f"Tab rekap untuk {facilitator_name} tidak ketemu — hubungi admin.")
        return best

    def _append_rekap_record(self, tab: str, rec: RekapRecord) -> int:
        ws = self._sheet_in(self.cfg.rekap_sheet_id, tab)
        row_data = rec.as_row()  # B..W (22 cols)
        # Kolom L: HYPERLINK klikabel "nama file" -> URL (format sama kayak data lama)
        if rec.bukti.startswith("http") and rec.bukti_name:
            url = rec.bukti.replace('"', "")
            name = rec.bukti_name.replace('"', "")
            row_data[10] = f'=HYPERLINK("{url}","{name}")'
        all_vals = self._cached_rows(self.cfg.rekap_sheet_id, tab)
        insert_row = len(all_vals) + 1
        for i, r in enumerate(all_vals):
            if i == 0:
                continue  # header
            if not any((c or "").strip() for c in r[1:12]):
                insert_row = i + 1
                break
        no = str(insert_row - 1)
        ws.update(f"A{insert_row}:W{insert_row}", [[no] + row_data], value_input_option="USER_ENTERED")
        self._invalidate_rows(self.cfg.rekap_sheet_id, tab)
        _rekap_status_cache.clear()
        log.info("Wrote rekap %s/%s at %s row %d", rec.kode, rec.pertemuan, tab, insert_row)
        return insert_row

    async def append_rekap_record(self, tab: str, rec: RekapRecord) -> int:
        return await self._run(partial(self._append_rekap_record, tab, rec))

    async def find_rekap_tab(self, facilitator_name: str) -> str:
        import time as _t
        key = self._normalize(facilitator_name)
        hit = _rekap_tab_cache.get(key)
        if hit and _t.time() - hit[0] < 600:
            return hit[1]
        res = await self._run(partial(self._find_rekap_tab, facilitator_name))
        _rekap_tab_cache[key] = (_t.time(), res)
        return res

    def _get_rekap_done(self, facilitator_name: str) -> set[tuple[str, str]]:
        """Set of (kode, tanggal) already in user's rekap tab."""
        try:
            tab = self._find_rekap_tab(facilitator_name)
            rows = self._cached_rows(self.cfg.rekap_sheet_id, tab)
        except SheetsError:
            return set()
        done = set()
        for r in rows[1:]:
            if len(r) <= 4:
                continue
            kode = r[4].strip().casefold() if len(r) > 4 else ""
            tgl = r[1].strip() if len(r) > 1 else ""
            if kode and tgl:
                done.add((kode, tgl))
        return done

    async def get_rekap_done(self, facilitator_name: str) -> set[tuple[str, str]]:
        return await self._run(partial(self._get_rekap_done, facilitator_name))

    def _rekap_match_rows(self, tab: str, kode: str, tanggal_list: list) -> list:
        """Rows (1-based idx, vals) matching kode + any accepted tanggal."""
        rows = self._cached_rows(self.cfg.rekap_sheet_id, tab)
        out = []
        for i, r in enumerate(rows):
            if i == 0 or len(r) <= 4:
                continue
            if r[4].strip().casefold() == kode.strip().casefold() and r[1].strip() in tanggal_list:
                out.append((i + 1, r))
        return out

    def _rekap_row_status(self, tab: str, kode: str, tanggal_list: list) -> dict:
        """none | complete | incomplete(+row idx, gaps). Gaps cover B-L + Total."""
        labels = ["Tanggal", "Dosen", "Jam", "Kode", "Matkul", "SKS",
                  "Pertemuan", "Tipe", "Sesi", "Peran", "Bukti"]
        try:
            rows = self._rekap_match_rows(tab, kode, tanggal_list)
        except (gspread.exceptions.WorksheetNotFound, SheetsError):
            return {"state": "none"}
        if not rows:
            return {"state": "none"}
        for idx, r in rows:
            missing = [labels[j] for j in range(11)
                       if not (len(r) > 1 + j and r[1 + j].strip())]
            if len(r) <= 14 or not r[14].strip():
                missing.append("Total")
            if missing:
                return {"state": "incomplete", "row": idx, "gaps": missing, "vals": r}
        return {"state": "complete", "count": len(rows)}

    async def rekap_row_status(self, tab: str, kode: str, tanggal_list: list) -> dict:
        return await self._run(partial(self._rekap_row_status, tab, kode, tanggal_list))

    def _rekap_row_values(self, tab: str, row_idx: int) -> list[str]:
        rows = self._cached_rows(self.cfg.rekap_sheet_id, tab)
        return rows[row_idx - 1] if 0 < row_idx <= len(rows) else []

    async def rekap_row_values(self, tab: str, row_idx: int) -> list[str]:
        return await self._run(partial(self._rekap_row_values, tab, row_idx))

    def _update_rekap_cells(self, tab: str, row_idx: int, cells: dict) -> None:
        data = [{"range": f"{col}{row_idx}", "values": [[val]]} for col, val in cells.items() if val != ""]
        if data:
            self._ss(self.cfg.rekap_sheet_id).values_batch_update(
                {"valueInputOption": "USER_ENTERED", "data": data})
            self._invalidate_rows(self.cfg.rekap_sheet_id, tab)
        log.info("Updated rekap %s row %d cols %s", tab, row_idx, sorted(cells))

    async def update_rekap_cells(self, tab: str, row_idx: int, cells: dict) -> None:
        await self._run(partial(self._update_rekap_cells, tab, row_idx, cells))

    def _get_rekap_status(self, facilitator_name: str) -> tuple[set, set]:
        """(complete, incomplete) sets of (kode, tanggal).
        Complete = B..L all filled AND O filled. T-W excluded (manual/akademik)."""
        try:
            tab = self._find_rekap_tab(facilitator_name)
            rows = self._cached_rows(self.cfg.rekap_sheet_id, tab)
        except SheetsError:
            return set(), set()
        complete, incomplete = set(), set()
        for r in rows[1:]:
            if len(r) <= 4:
                continue
            kode = r[4].strip().casefold() if len(r) > 4 else ""
            tgl = r[1].strip() if len(r) > 1 else ""
            if not kode or not tgl:
                continue
            core = [(r[i].strip() if len(r) > i else "") for i in range(1, 12)]  # B..L
            o_val = r[14].strip() if len(r) > 14 else ""
            if all(core) and o_val:
                complete.add((kode, tgl))
            else:
                incomplete.add((kode, tgl))
        return complete, incomplete

    async def get_rekap_status(self, facilitator_name: str) -> tuple[set, set]:
        import time as _t
        key = self._normalize(facilitator_name)
        hit = _rekap_status_cache.get(key)
        if hit and _t.time() - hit[0] < _CACHE_TTL:
            return hit[1]
        res = await self._run(partial(self._get_rekap_status, facilitator_name))
        _rekap_status_cache[key] = (_t.time(), res)
        return res

    def _sheet_rows(self, title: str) -> list[list[str]]:
        return self._cached_rows(self.cfg.sheet_id, title)

    async def sheet_rows(self, title: str) -> list[list[str]]:
        return await self._run(partial(self._sheet_rows, title))

    @staticmethod
    def _norm_dosen(name: str) -> list[str]:
        titles = {"s", "st", "m", "mt", "si", "kom", "t", "e", "mm", "msi", "mcs",
                  "mti", "mtI", "sc", "bsc", "mphil", "phd", "dr", "c", "mkom",
                  "skom", "ak", "ca", "cpa", "aust", "mba", "ma", "msc", "meng",
                  "eng", "psy", "psi", "mikom", "mh", "mhkes", "cfp"}
        toks = re.sub(r"[^a-z ]", " ", (name or "").lower()).split()
        return [t for t in toks if t not in titles and len(t) > 1]

    def _school_for_prodi(self, prodi_tab: str) -> str:
        pn = self._normalize(prodi_tab)
        try:
            import json
            from pathlib import Path
            mp = Path(__file__).parent / "data" / "school_map.json"
            if mp.exists():
                custom = json.loads(mp.read_text(encoding="utf-8"))
                for school, keys in custom.items():
                    if any(self._normalize(k) in pn for k in keys):
                        return school
        except Exception:
            pass
        for school, keys in SCHOOL_KEYWORDS.items():
            if any(k in pn for k in keys):
                return school
        return ""

    def _feedback_counts(self, kode: str, pertemuan: str, dosen: str, prodi_tab: str) -> dict:
        """Q = distinct NIM with clean match: kode + pertemuan + dosen + prodi + school."""
        import time
        now = time.time()
        if _feedback_cache["rows"] is None or now - _feedback_cache["ts"] > 600:
            ss = self._client().open_by_key("1dZQcq3TvPh7wkW0z8SF94YExs5jONYf_O3oV09Hk604")
            _feedback_cache["rows"] = ss.worksheet("Form Responses 1").get_all_values()
            _feedback_cache["ts"] = now
        rows = _feedback_cache["rows"]
        kc = kode.strip().casefold()
        nums = set(re.findall(r"\d+", pertemuan or ""))
        d_toks = self._norm_dosen(dosen)
        school_exp = self._school_for_prodi(prodi_tab or "").casefold()
        pn = self._normalize(prodi_tab or "")
        seen, schools = set(), {}
        for r in rows[1:]:
            if len(r) <= 26:
                continue
            nim = r[2].strip() if len(r) > 2 else ""
            if not nim or nim in seen:
                continue
            blob = " ".join(c for c in r[8:25]).casefold()
            if kc not in blob:
                continue
            aa = re.findall(r"\d+", r[26].strip() if len(r) > 26 else "")
            if nums and not (set(aa) & nums):
                continue
            f_toks = self._norm_dosen(r[25] if len(r) > 25 else "")
            if d_toks and f_toks:
                if not (d_toks[-1] == f_toks[-1] and (d_toks[0] == f_toks[0] or len(d_toks) == 1 or len(f_toks) == 1)):
                    continue
            if pn and not re.search(r"\b" + re.escape(pn) + r"\b", blob):
                # fallback: match any major cell equality
                majors = {c.strip().casefold() for c in r[8:25:3] if c.strip()}
                if pn not in majors and not any(pn in m or m in pn for m in majors):
                    continue
            if school_exp:
                sch = r[6].strip().casefold() if len(r) > 6 else ""
                if sch != school_exp:
                    continue
            seen.add(nim)
            schools[r[6].strip() if len(r) > 6 else ""] = schools.get(r[6].strip() if len(r) > 6 else "", 0) + 1
        return {"q": len(seen), "schools": schools}

    async def feedback_counts(self, kode: str, pertemuan: str, dosen: str, prodi_tab: str) -> dict:
        return await self._run(partial(self._feedback_counts, kode, pertemuan, dosen, prodi_tab))

    def _bukti_folder_for(self, drive, facilitator: str) -> str:
        """Per-fasil subfolder inside REKAP_BUKTI_FOLDER_ID (cached in data/rekap_folders.json)."""
        import json
        from pathlib import Path
        cache_path = Path(__file__).parent / "data" / "rekap_folders.json"
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
        except Exception:
            cache = {}
        if facilitator in cache:
            return cache[facilitator]
        parent = (self.cfg.rekap_bukti_folder_id or "").strip()
        if not parent:
            raise SheetsError("Folder Bukti belum diset (REKAP_BUKTI_FOLDER_ID) — hubungi admin.")
        # Pakai subfolder yang sudah ada bila cocok (mis. "Rayhan Dwiyanto")
        try:
            existing = drive.files().list(
                q=f"'{parent}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                fields="files(id,name)", pageSize=100).execute().get("files", [])
            fnorm = self._normalize(facilitator)
            best, best_len = None, 0
            for f in existing:
                n = self._normalize(f.get("name", ""))
                if n and (n in fnorm or fnorm in n) and len(n) > best_len:
                    best, best_len = f["id"], len(n)
            if best:
                cache[facilitator] = best
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
                return best
        except SheetsError:
            raise
        except Exception:
            pass
        folder = drive.files().create(
            body={"name": facilitator, "mimeType": "application/vnd.google-apps.folder",
                  "parents": [parent]}, fields="id").execute()
        cache[facilitator] = folder["id"]
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return folder["id"]

    def _upload_bukti(self, data: bytes, filename: str, mimetype: str, facilitator: str = "") -> str:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload
        import io
        creds = Credentials.from_service_account_file(str(self.cfg.service_account_json), scopes=SCOPES)
        drive = build("drive", "v3", credentials=creds)
        folder = self._bukti_folder_for(drive, facilitator.strip() or "Lainnya")
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mimetype or "image/jpeg")
        f = drive.files().create(body={"name": filename, "parents": [folder]},
                                 media_body=media, fields="id,webViewLink").execute()
        try:
            drive.permissions().create(fileId=f["id"], body={"type": "anyone", "role": "reader"}).execute()
        except Exception:
            pass
        return f.get("webViewLink", "")

    async def upload_bukti(self, data: bytes, filename: str, mimetype: str, facilitator: str = "") -> str:
        return await self._run(partial(self._upload_bukti, data, filename, mimetype, facilitator))

    async def append_record(self, rec: LogRecord) -> None:
        await self._run(partial(self._append_record, rec))

    async def append_backup_record(self, rec: BackupRecord) -> None:
        await self._run(partial(self._append_backup_record, rec))

    async def append_cancel_record(self, rec: CancelRecord) -> None:
        await self._run(partial(self._append_cancel_record, rec))

    async def warmup(self) -> None:
        """Seed caches at startup (bot.py post_init) and re-seed via periodic JobQueue
        refresh. Best-effort: failures are logged, never crash boot."""
        try:
            await self._run(self._warmup)
        except Exception as exc:
            log.warning("Sheets warmup failed: %s", exc)

    def _warmup(self) -> None:
        for title in (self.cfg.master_sheet, self.cfg.zoom_record_sheet, self.cfg.backup_sheet):
            _rows_cache.pop((self.cfg.sheet_id, title), None)  # force refresh, ignore TTL
            self._cached_rows(self.cfg.sheet_id, title)

    async def _run(self, fn):
        loop = asyncio.get_running_loop()
        async with self._lock:  # serialize to dodge quota races
            try:
                # 60s wall-clock cap. On timeout the lock is released (async with
                # unwinds) — the executor thread keeps running but is orphaned, so
                # no deadlock and no blocked event loop.
                return await asyncio.wait_for(loop.run_in_executor(None, fn), timeout=60)
            except asyncio.TimeoutError as exc:
                log.exception("sheets call timed out")
                raise SheetsError("Google Sheets timeout (60 detik) — coba lagi.") from exc
            except SheetsError:
                raise
            except gspread.exceptions.GSpreadException as exc:
                log.exception("gspread failure")
                raise SheetsError(f"Gagal mengakses Google Sheets: {exc}") from exc
            except OSError as exc:
                log.exception("file/network failure")
                raise SheetsError("Gagal membaca kredensial/jaringan. Pastikan berkas kredensial tersedia.") from exc
            except Exception as exc:
                log.exception("unexpected sheets failure")
                raise SheetsError("Terjadi kesalahan saat mengakses Google Sheets. Coba lagi.") from exc


def this_week_classes(classes: list[ClassEntry]) -> dict[str, list[ClassEntry]]:
    """Group by Indonesian day name; caller renders whole week (sheet is weekly-recurring)."""
    by_day: dict[str, list[ClassEntry]] = {}
    for c in classes:
        norm = re.sub(r"[^a-z]", "", c.day.lower())
        day = next((d for d in DAY_ORDER if re.sub(r"[^a-z]", "", d.lower()) == norm), None)
        if day is None:
            continue
        by_day.setdefault(day, []).append(c)
    for lst in by_day.values():
        lst.sort(key=lambda c: c.start_time)
    return by_day


def today_day_wib() -> str:
    return DAY_ORDER[datetime.now(WIB).weekday()]
