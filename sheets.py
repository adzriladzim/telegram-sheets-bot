"""Google Sheets access via gspread.

gspread is synchronous — every public function here is an async wrapper that
runs the blocking call in the default executor (loop.run_in_executor).
"""
from __future__ import annotations

import asyncio
import logging
import re
import socket
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial

import gspread
import gspread.exceptions
import googleapiclient.errors
from google.oauth2.service_account import Credentials

from config import Config

log = logging.getLogger(__name__)


def _q(title: str) -> str:
    """Quote a tab title for A1 ranges, escaping apostrophes (' -> '')."""
    return "'%s'" % title.replace("'", "''")


def _row_has_data(row: list) -> bool:
    """True if the row has any filled cell in B..L (the rekap data columns)."""
    return any((c or "").strip() for c in row[1:12])

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
# Rekap per-fasil separator rows (block-boundary empty rows + heavy-border rows).
# (ss_id, title) -> (ts, [0-based row indexes]); TTL = _ROWS_TTL, invalidated on
# rekap writes. The campus draws a thick black line under each closed period;
# new records must go BELOW the LAST such line, never on it.
_separator_cache: dict[tuple[str, str], tuple[float, list[int]]] = {}
_ROWS_TTL = 300
# Border styles that read as a "heavy" separator line (marginally SOLID_THICK).
# Real tabs observed so far use an EMPTY row as separator; heavy borders are a
# fallback for when campus styles the line itself.
_BORDER_HEAVY = {"SOLID_THICK", "SOLID_MEDIUM", "DOUBLE"}
_BORDER_MIN_WIDTH = 4
# Worksheet titles in the Rekap spreadsheet that are NOT per-fasil tabs.
_SYSTEM_TABS = ("PENTING DIBACA", "Template")
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
    "https://www.googleapis.com/auth/drive",
]

WIB = timezone(timedelta(hours=7), name="WIB")

# Master schedule columns (0-indexed) — Batch 5 Ganjil 26/27
# Header: [0]Hari [1]NamaFasil [2]Kategori [3]Jam [4]Kode [5]MK [6]Dosen [7]Ruang [8]RomBel [9]SKS [10]ZoomNo [11]ZoomLink [12]Keterangan [13]KetuaKelas [14]HPKetua
COL_DAY, COL_FASIL, COL_KATEGORI, COL_JAM, COL_KODE = 0, 1, 2, 3, 4
COL_MK, COL_DOSEN, COL_RUANG, COL_ROMBEL, COL_SKS, COL_ZOOM_NO, COL_ZOOM_LINK, COL_KETERANGAN = 5, 6, 7, 8, 9, 10, 11, 12

# Cancel sheet columns (0-indexed, tab 'Kelas Cancel & Pengganti' — bot READ-ONLY:
# TIDAK pernah menulis kolom J..R di tab ini; make-up hanya dibaca):
# A No. B Dosen C Matkul D Kode E Sesi F JadwalAwal G Jam H SKS I Fasil(cancel)
# J StatusTerlaksana(T/F) K kosong L Jadwal Make-up M Jam N Fasil(make-up)
# O Zoom(nomor) P Zoom(link) Q Room R Ket.
CNL_DOSEN, CNL_MATKUL, CNL_KODE = 1, 2, 3
CNL_SESI, CNL_JADWAL_AWAL, CNL_JAM, CNL_SKS = 4, 5, 6, 7
CNL_FASIL, CNL_STATUS, CNL_JADWAL_MAKEUP, CNL_JAM_MAKEUP = 8, 9, 11, 12
CNL_FASIL_MAKEUP, CNL_ZOOM_NO, CNL_ZOOM_LINK, CNL_ROOM, CNL_KET = 13, 14, 15, 16, 17

TIPE_KELAS_MAP = {"reguler": "Reguler", "professional": "Professional", "akselerasi": "Akselerasi", "akselerasi & professional": "Akselerasi & Professional", "professional & akselerasi": "Akselerasi & Professional", "pro": "Professional", "ae": "Akselerasi", "ae & pro": "Akselerasi & Professional", "pro & ae": "Akselerasi & Professional"}
DAY_ORDER = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

# Chars illegal in Google Drive filenames (plus '_' — reserved as our separator).
_ILLEGAL_FILENAME = re.compile(r'[/\\:*?"<>|_\x00-\x1f]')


def _safe_filename_part(part: str, fallback: str = "unnamed") -> str:
    """Sanitize one filename segment for Google Drive (strip illegal chars, collapse spaces)."""
    out = _ILLEGAL_FILENAME.sub("-", (part or "").strip())
    out = re.sub(r"\s+", " ", out).strip(" .")
    return out or fallback


def _api_reason(exc) -> str:
    """Reason code from a Drive/Sheets HttpError (exc.error_details), e.g. storageQuotaExceeded. Best-effort."""
    import json
    det = getattr(exc, "error_details", None) or []
    if not isinstance(det, list):
        det = [det]
    for d in det:
        if isinstance(d, dict) and d.get("reason"):
            return str(d["reason"])
    try:
        err = json.loads((exc.content or b"").decode("utf-8")).get("error", {})
        for e in err.get("errors", []) or []:
            if e.get("reason"):
                return str(e["reason"])
    except Exception:
        pass
    return ""


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
        """Columns B..I per live header of Kelas Cancel & Pengganti:
        B Nama Dosen, C Nama Mata Kuliah, D Kode Kelas, E Sesi, F Jadwal Awal,
        G Jam, H SKS, I Fasil. Col A (No.) prepended at append site."""
        return [
            self.lecturer,     # B
            self.subject,      # C
            self.kode,         # D
            self.sesi,         # E
            self.jadwal_awal,  # F
            self.jam,          # G
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
        # Live header: G='Pertemuan ke-', H='SKS'. (Pernah terbalik: sks->G,
        # pertemuan->H — bot tulis jadi Pertemuan 3, SKS 2. Diswap 2026-09-17.)
        return [
            self.tanggal, self.lecturer, self.jam, self.kode, self.subject,
            self.pertemuan, self.sks, self.tipe, self.sesi, self.peran,
            self.bukti, "", "",
            self.total, self.hadir, self.feedback, self.tidak, self.belum,
            "", "", "", "",
        ]

    def bukti_filename(self, ext: str = "jpg") -> str:
        """Drive filename per reference format {tanggal}_{dosen}_{matkul}_{nama_fasil}.{ext}."""
        ext = (ext or "jpg").lstrip(".") or "jpg"
        parts = (
            _safe_filename_part(self.tanggal, "tanggal"),
            _safe_filename_part(self.lecturer, "dosen"),
            _safe_filename_part(self.subject, "matkul"),
            _safe_filename_part(self.facilitator, "fasil"),
        )
        return "_".join(parts) + "." + ext


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


class SheetsClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._gc: gspread.Client | None = None
        self._lock = asyncio.Lock()  # single-flight for ALL gspread I/O
        self._chat_locks: dict[int, asyncio.Lock] = {}
        # Safety net (jaring terakhir): global socket default so any Google call
        # that misses its explicit per-client timeout can't block a thread
        # forever. Explicit timeouts are also set below (gspread/Drive).
        socket.setdefaulttimeout(30)

    # ---------- sync internals ----------

    def _client(self) -> gspread.Client:
        if self._gc is None:
            creds = Credentials.from_service_account_file(str(self.cfg.service_account_json), scopes=SCOPES)
            self._gc = gspread.authorize(creds)
            # HTTPClient timeout (connect, read): without it a wedged request
            # holds the single-flight lock forever -> bot goes silent.
            self._gc.set_timeout((10, 30))
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

    def _grid_heavy_rows(self, ss_id: str, title: str) -> list[int]:
        """0-based indexes whose B..W cells carry a HEAVY border (SOLID_THICK /
        SOLID_MEDIUM / DOUBLE, or width >= 4). Campus's thick separator line —
        where present — is just an empty row, so this is a fallback signal."""
        ws = self._sheet_in(ss_id, title)
        try:
            resp = self._client().http_client.spreadsheets_get(
                ss_id,
                params={
                    "ranges": f"{_q(title)}!A1:W{ws.row_count}",
                    "includeGridData": "true",
                    "fields": "sheets.data.rowData.values.userEnteredFormat",
                },
            )
        except gspread.exceptions.APIError as exc:
            raise SheetsError(f"Google API error: {exc.response.status_code}") from exc
        out: list[int] = []
        try:
            data = resp["sheets"][0]["data"][0]
        except (KeyError, IndexError):
            return out
        for i, row in enumerate(data.get("rowData", [])[:ws.row_count]):
            heavy = False
            for cell in (row.get("values") or [])[:23]:
                if not isinstance(cell, dict):
                    continue
                b = (cell.get("userEnteredFormat") or {}).get("borders") or {}
                for side in ("top", "bottom"):
                    eb = b.get(side)
                    if eb and (eb.get("style") in _BORDER_HEAVY
                               or eb.get("width", 0) >= _BORDER_MIN_WIDTH):
                        heavy = True
                        break
                if heavy:
                    break
            if heavy:
                out.append(i)
        return out

    def _separator_rows(self, ss_id: str, title: str) -> list[int]:
        """0-based indexes of rekap separator lines for a per-fasil tab.

        Two signals, OR-ed:
          1. block-boundary empty row — a row with no B..L values sitting
             directly below a data row (this is how campus draws the black
             line in the real tabs: an empty styled row);
          2. a B..W cell with a HEAVY border (fallback for styled lines).
        Cached like _rows_cache; invalidated on rekap writes.
        """
        key = (ss_id, title)
        hit = _separator_cache.get(key)
        if hit and time.time() - hit[0] < _ROWS_TTL:
            return hit[1]
        seps: set[int] = set()
        rows = self._cached_rows(ss_id, title)
        for i in range(1, len(rows)):
            if not _row_has_data(rows[i]) and _row_has_data(rows[i - 1]):
                seps.add(i)
        try:
            seps.update(self._grid_heavy_rows(ss_id, title))
        except SheetsError:
            log.warning("heavy-border scan failed for %s/%s — block-gap seps only",
                        ss_id, title)
        out = sorted(seps)
        _separator_cache[key] = (time.time(), out)
        return out

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

    @staticmethod
    def _col_num(letter: str) -> int:
        """'A' -> 1, 'B' -> 2, ... (1-based column index)."""
        n = 0
        for ch in letter.upper():
            n = n * 26 + (ord(ch) - ord("A") + 1)
        return n

    def _guard_grid(self, ws: gspread.Worksheet, col_letters: list[str], row: int) -> None:
        """Reject write targets outside the worksheet grid with a clear error.
        Rows: allow up to 1024 past the final grid bound — Sheets auto-expands
        on write, and a full tab must not hard-fail saves. Columns stay strict:
        an over-wide target means we resolved the wrong tab."""
        max_col = max(self._col_num(c) for c in col_letters) if col_letters else 0
        if row > ws.row_count + 1024:
            raise SheetsError(
                f"Target baris {row} melebihi batas sheet '{ws.title}' "
                f"({ws.row_count} baris) — tab penuh atau tab salah. Hubungi admin.")
        if max_col > ws.col_count:
            raise SheetsError(
                f"Target kolom melebihi batas sheet '{ws.title}' "
                f"({ws.col_count} kolom) — tab salah. Hubungi admin.")

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
        self._guard_grid(ws, list(unprotected_map.values()), insert_row)
        # Worksheet-scoped: qualify every range with the tab title so a bare
        # "B5" can never land on the spreadsheet's first sheet. gspread's
        # Worksheet has no values_batch_update (6.2.1) — same qualified pattern
        # as _update_absen.
        data = [{"range": f"{_q(ws.title)}!{col}{insert_row}", "values": [[row_data[idx]]]}
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

    def _zoom_entries(self, name: str) -> list[dict]:
        """Zoom Record rows milik fasil (col C match). Read-only wrapper untuk
        picker /rekap — 1 baris Zoom = 1 baris rekap potensial."""
        rows = self._cached_rows(self.cfg.sheet_id, self.cfg.zoom_record_sheet)
        target = self._normalize(name)
        out: list[dict] = []
        for i, r in enumerate(rows):
            if i == 0:
                continue  # header
            if len(r) <= 14:
                continue
            if self._normalize(r[2]) != target:
                continue
            if not any((c or "").strip() for c in r[1:15]):
                continue  # skip baris kosong
            kode = r[5].strip()
            if not kode:
                continue
            out.append({
                "kode": kode,                    # F
                "subject": r[6].strip(),         # G
                "tanggal": self._norm_date(r[3].strip()),  # D -> dd/mm/yyyy
                "pertemuan": r[7].strip(),       # H
                "scheme": r[8].strip(),          # I
                "sks": r[9].strip(),             # J
                "tipe": r[10].strip(),           # K
                "dosen": r[11].strip(),          # L
                "mulai": r[12].strip(),          # M
                "zoom": r[13].strip(),           # N
                "catatan": r[14].strip(),        # O
                "row": i + 1,
            })
        return out

    async def zoom_entries(self, name: str) -> list[dict]:
        return await self._run(partial(self._zoom_entries, name))

    def _all_absen_rows(self) -> dict[str, list[list[str]]]:
        """All absen worksheet rows {title: rows} in ONE values.batchGet, cached per title."""
        titles = self._tabs(self.cfg.absen_sheet_id)
        now = time.time()
        need = [t for t in titles
                if not _rows_cache.get((self.cfg.absen_sheet_id, t))
                or now - _rows_cache[(self.cfg.absen_sheet_id, t)][0] >= _ROWS_TTL]
        if need:
            resp = self._ss(self.cfg.absen_sheet_id).values_batch_get([_q(t) for t in need])
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
                if r and r[0].strip() == "Kode Kelas" and len(r) > 1 and r[1].strip():
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
                if rows[r_idx] and rows[r_idx][0].strip() == "Program Studi":
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

    def _absen_coverage(self) -> dict[str, set[int]]:
        """Per Kode: set of pertemuan (1-16) that have ANY student status filled,
        aggregated across all absen blocks/tabs in ONE pass over cached rows."""
        filled: dict[str, set[int]] = {}
        for rows in self._all_absen_rows().values():
            for i, r in enumerate(rows):
                if not (r and r[0].strip() == "Kode Kelas" and len(r) > 1 and r[1].strip()):
                    continue
                kode = r[1].strip()
                nh = -1
                for j in range(i, min(i + 10, len(rows))):
                    if rows[j] and rows[j][0].strip() == "NIM":
                        nh = j
                        break
                if nh == -1:
                    continue
                s = filled.setdefault(kode, set())
                for r2 in rows[nh + 2:]:
                    if r2 and r2[0].strip() == "Program Studi":
                        break
                    # Row right under the session-number row holds the filler's
                    # NAME in the meeting columns (no NIM/Nama). Skip no-id rows
                    # so a stamped name can't fake a filled meeting (same guard
                    # _absen_counts/_list_students already apply).
                    if not r2 or not (r2[0].strip() or (len(r2) > 1 and r2[1].strip())):
                        continue
                    for p in range(1, 17):
                        col = 3 + (p - 1)
                        if len(r2) > col and r2[col].strip():
                            s.add(p)
        return filled

    async def absen_coverage(self) -> dict[str, set[int]]:
        return await self._run(self._absen_coverage)

    def _locate_absen_block(self, kode: str) -> list[tuple[str, list[list[str]], int]]:
        """Return ALL absen blocks (title, rows, nim_header) matching a Kode across
        every prodi sheet — a class can have several blocks (multi-prodi), so we
        never stop at the first match."""
        key = kode.casefold()
        found = []
        for title, rows in self._all_absen_rows().items():
            for i, r in enumerate(rows):
                if r and r[0].strip() == "Kode Kelas" and len(r) > 1 and r[1].strip().casefold() == key:
                    nim_header = -1
                    for j in range(i, min(i + 10, len(rows))):
                        if rows[j] and rows[j][0].strip() == "NIM":
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
                if rows[r_idx] and rows[r_idx][0].strip() == "Program Studi":
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

    # Zoom Cakrawala display-name: "NNN_Nama Lengkap_Prodi" (NNN = 3 digit akhir NIM).
    # Prodi segment: short, variant (If/SI/Ak) — NOT validated, hanya pemisah regex.
    _ZOOM_DISPLAY_RE = re.compile(r"^(\d{3})_(.+?)_[A-Za-z0-9&'\-\s]+$")

    def _resolve_absen(self, kode: str, identifiers: list[str]) -> dict:
        """Resolve identifiers to student rows across ALL matching absen blocks.
        NIM wajib exact-penuh, Nama contains, Zoom display-name "NNN_Nama_Prodi".
        Returns {blocks, matched: [(row_idx, nim, nama, mode, title)],
        ambiguous: {ident: [nama]}, unmatched: [ident]}."""
        blocks = self._locate_absen_block(kode)
        roster = []
        for title, rows, nim_header in blocks:
            for r_idx in range(nim_header + 2, len(rows)):
                if rows[r_idx] and rows[r_idx][0].strip() == "Program Studi":
                    break
                nim = rows[r_idx][0].strip() if len(rows[r_idx]) > 0 else ""
                nama = rows[r_idx][1].strip() if len(rows[r_idx]) > 1 else ""
                mode = rows[r_idx][2].strip() if len(rows[r_idx]) > 2 else ""
                if nim or nama:
                    roster.append((r_idx, nim, nama or "-", mode, title))
        matched, ambiguous, unmatched = [], {}, []

        def _accept(hits: list) -> bool:
            """Single hit → accept; else leave to caller for ambiguous/unmatched."""
            if len(hits) == 1 and hits[0] not in matched:
                matched.append(hits[0])
                return True
            return False

        for ident in identifiers:
            norm = self._normalize(ident)
            if not norm:
                continue
            dn = self._ZOOM_DISPLAY_RE.match(ident)
            if dn:
                digits, core = dn.group(1), self._normalize(dn.group(2))
                hits = [e for e in roster if core and core in self._normalize(e[2])]
                if _accept(hits):
                    continue
                if len(hits) > 1:
                    # Tiebreaker: NIM penuh endsWith 3 digit. Tanpa digit cocok → AMBIGU.
                    digit_hits = [e for e in hits if e[1].strip().endswith(digits)]
                    if _accept(digit_hits):
                        continue
                    ambiguous[ident] = [e[2] for e in hits[:5]]
                    continue
                # regex cocok tapi nama tak ketemu → jatuh ke jalur normal (unmatched)
            if norm.isdigit():
                hits = [e for e in roster if e[1].strip() == norm]
            else:
                hits = [e for e in roster if norm in self._normalize(e[2])]
            if _accept(hits):
                continue
            if len(hits) > 1:
                ambiguous[ident] = [e[2] for e in hits[:5]]
            else:
                unmatched.append(ident)
        return {"blocks": blocks, "matched": matched,
                "ambiguous": ambiguous, "unmatched": unmatched,
                "title": blocks[0][0] if blocks else "",
                "rows": blocks[0][1] if blocks else [],
                "nim_header": blocks[0][2] if blocks else -1}

    def _update_absen(self, kode: str, pertemuan: int, identifiers: list[str], status: str,
                      pengisi: str = "") -> dict:
        res = self._resolve_absen(kode, identifiers)
        if not 1 <= pertemuan <= 16:
            raise SheetsError("Pertemuan harus 1-16")
        col_idx = 3 + (pertemuan - 1)
        col_letter = chr(ord('A') + col_idx)
        updated = 0
        data = []
        written_titles = set()
        for r_idx, nim, nama, mode, title in res["matched"]:
            data.append({"range": f"{_q(title)}!{col_letter}{r_idx+1}", "values": [[status]]})
            written_titles.add(title)
            updated += 1
        # Sheet convention (client manual habit, verified live by probe): a block
        # header is three rows under "Kode Kelas" — row nim_header = "NIM/Nama
        # Mahasiswa/Mode", row nim_header+1 (0-based) = the meeting NUMBERS row
        # (D=1..S=16), row nim_header+2 (0-based) = the filler NAME row where
        # humans write the facilitator's full name in the meeting column right
        # below its number. In A1 notation (1-based rows) that name row sits at
        # nh+3. The name overwrites in place, last filler wins. One write per
        # touched block so a multi-prodi class is stamped in every sheet.
        pengisi = (pengisi or "").strip()
        header_cells: list[str] = []
        if data and pengisi:
            # A tab can hold several blocks of the same kode — stamp each one.
            nim_headers: dict[str, list[int]] = {}
            for t, _rows, nh in res["blocks"]:
                nim_headers.setdefault(t, []).append(nh)
            for title in sorted(written_titles):
                for nh in nim_headers.get(title, []) or []:
                    if nh >= 0:
                        header_cells.append(f"{_q(title)}!{col_letter}{nh + 3}")
                        data.append({"range": header_cells[-1], "values": [[pengisi]]})
        if data:
            self._ss(self.cfg.absen_sheet_id).values_batch_update(
                {"valueInputOption": "USER_ENTERED", "data": data})
            for title in written_titles:
                self._invalidate_rows(self.cfg.absen_sheet_id, title)
        self._invalidate_absen_students(kode)
        log.info("Updated absen %s pertemuan %d status %s: %d rows (%s)%s",
                 kode, pertemuan, status, updated, ",".join(sorted(written_titles)) or "-",
                 f" pengisi={pengisi}" if pengisi else "")
        if updated == 0 and not res["ambiguous"] and not res["unmatched"]:
            raise SheetsError("Tidak ada NIM/Nama yang cocok")
        return {"updated": updated, "ambiguous": res["ambiguous"],
                "unmatched": res["unmatched"],
                "names": [nm for _, _, nm, _, _ in res["matched"][:10]],
                "sheets": sorted(written_titles),
                "sheet": res["title"],
                "pengisi": pengisi,
                "header_cells": header_cells}

    async def update_absen(self, kode: str, pertemuan: int, identifiers: list[str], status: str,
                           pengisi: str = "") -> dict:
        return await self._run(partial(self._update_absen, kode, pertemuan, identifiers, status, pengisi))

    def _master_lookup(self, kode: str, room_key: str = "") -> tuple[str, str, str, str]:
        """(zoom_no, sks, semester, ket_master) from master sheet by kode.
        Kode can repeat across RomBel: collect ALL master rows with that kode,
        prefer the one whose RomBel (col 8) matches room_key, else first match."""
        try:
            mrows = self._cached_rows(self.cfg.sheet_id, self.cfg.master_sheet)
        except SheetsError:
            return "", "", "", ""
        candidates = [
            mr for mr in mrows
            if len(mr) > COL_KODE and mr[COL_KODE].strip().casefold() == kode.casefold()
        ]
        chosen = None
        rk = self._normalize(room_key)
        if rk:
            for mr in candidates:
                if len(mr) > COL_ROMBEL and self._normalize(mr[COL_ROMBEL]) == rk:
                    chosen = mr
                    break
        if chosen is None and candidates:
            chosen = candidates[0]  # fallback: first kode match
        if chosen is None:
            return "", "", "", ""
        zoom_no = chosen[COL_ZOOM_NO].strip() if len(chosen) > COL_ZOOM_NO else ""
        sks = chosen[COL_SKS].strip() if len(chosen) > COL_SKS else ""
        rombel_tmp = chosen[COL_ROMBEL].strip() if len(chosen) > COL_ROMBEL else ""
        sems = sorted(set(re.findall(r"\b(\d+)\b", rombel_tmp)))
        semester = " & ".join(sems) if sems else ""
        ket_master = chosen[COL_KETERANGAN].strip() if len(chosen) > COL_KETERANGAN else ""
        return zoom_no, sks, semester, ket_master

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
            # Lookup zoom/sks/semester/keterangan from master by kode (rombel-match
            # prefer backup Col H = room/rombel, else first kode match).
            zoom_no = ""
            sks = ""
            semester = ""
            ket_master = ""
            try:
                zoom_no, sks, semester, ket_master = self._master_lookup(
                    kode, row[7].strip() if len(row) > 7 else "")
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
                keterangan=(row[9].strip() if len(row) > 9 else "") or ket_master,
                backup_hari_tanggal=hari_tanggal,
                semester=semester,
                row_index=i,
            ))
        log.info("Fetched %d backup classes for %s", len(out), facilitator_name)
        return out

    async def get_backup_classes(self, facilitator_name: str) -> list[ClassEntry]:
        return await self._run(partial(self._fetch_backup_classes, facilitator_name))

    def _fetch_makeup_classes(self, facilitator_name: str) -> list[ClassEntry]:
        """Make-up classes from Cancel tab — READ-ONLY (bot never writes J..R).

        Baris = kelas yang di-cancel lalu dijadwalkan ulang (make-up). Filter:
        B (dosen) terisi, L (jadwal make-up) terisi, J (status terlaksana)
        casefold != 'true', N (fasil make-up) match nama (partial, spt backup)."""
        try:
            rows = self._cached_rows(self.cfg.sheet_id, self.cfg.cancel_sheet)
        except SheetsError:
            return []
        target = self._normalize(facilitator_name)
        out: list[ClassEntry] = []
        for i, row in enumerate(rows):
            if i == 0 or len(row) <= CNL_KET:
                continue  # header + short rows
            dosen = row[CNL_DOSEN].strip() if len(row) > CNL_DOSEN else ""
            if not dosen:
                continue
            status = row[CNL_STATUS].strip().casefold() if len(row) > CNL_STATUS else ""
            # J StatusTerlaksana: 'true'/'t' = kelas sudah terlaksana — bukan make-up
            # lagi. Live header pakai T/F; 'TRUE' juga ditangkap.
            if status in ("true", "t"):
                continue  # kelas sudah terlaksana — bukan make-up lagi
            hari_tanggal = row[CNL_JADWAL_MAKEUP].strip() if len(row) > CNL_JADWAL_MAKEUP else ""
            if not hari_tanggal:
                continue
            fasil_makeup = row[CNL_FASIL_MAKEUP].strip() if len(row) > CNL_FASIL_MAKEUP else ""
            if not fasil_makeup or target not in self._normalize(fasil_makeup):
                continue
            kode = row[CNL_KODE].strip() if len(row) > CNL_KODE else ""
            if not kode:
                continue
            day = (hari_tanggal.split(",")[0].strip() if "," in hari_tanggal
                   else (hari_tanggal.split()[0] if hari_tanggal else ""))
            # Semester dari master (rombel-match prefer Q=room, fallback kode pertama).
            zoom_no, sks, semester, ket_master = "", "", "", ""
            try:
                zoom_no, sks, semester, ket_master = self._master_lookup(
                    kode, row[CNL_ROOM].strip() if len(row) > CNL_ROOM else "")
            except Exception as exc:
                log.warning("makeup master lookup failed: %s", exc)
            out.append(ClassEntry(
                code=kode,
                subject=row[CNL_MATKUL].strip() if len(row) > CNL_MATKUL else "",
                day=day or "Senin",
                time_range=row[CNL_JAM_MAKEUP].strip() if len(row) > CNL_JAM_MAKEUP else "",
                category="Make-up",
                lecturer=dosen,
                room=row[CNL_ROOM].strip() if len(row) > CNL_ROOM else "",
                rombel="",
                sks=row[CNL_SKS].strip() if len(row) > CNL_SKS else "",
                zoom_number=row[CNL_ZOOM_NO].strip() if len(row) > CNL_ZOOM_NO else "",
                zoom_link=row[CNL_ZOOM_LINK].strip() if len(row) > CNL_ZOOM_LINK else "",
                keterangan=(row[CNL_KET].strip() if len(row) > CNL_KET else "") or ket_master,
                backup_hari_tanggal=hari_tanggal,
                semester=semester,
                row_index=i,
            ))
        log.info("Fetched %d makeup classes for %s", len(out), facilitator_name)
        return out

    async def get_makeup_classes(self, facilitator_name: str) -> list[ClassEntry]:
        return await self._run(partial(self._fetch_makeup_classes, facilitator_name))

    def _fetch_makeup_notes(self, facilitator_name: str) -> dict[str, tuple[str, str]]:
        """kode.casefold -> (L jadwal make-up, N fasil make-up) untuk baris dengan
        I (fasil cancel) match nama DAN L terisi. Dipakai /schedule: kelas milik
        user yang di-cancel dapat catatan '🧪 make-up {L}, {N}' di baris aslinya."""
        try:
            rows = self._cached_rows(self.cfg.sheet_id, self.cfg.cancel_sheet)
        except SheetsError:
            return {}
        target = self._normalize(facilitator_name)
        out: dict[str, tuple[str, str]] = {}
        for i, row in enumerate(rows):
            if i == 0 or len(row) <= CNL_FASIL_MAKEUP:
                continue
            owner = row[CNL_FASIL].strip() if len(row) > CNL_FASIL else ""
            if not owner or target not in self._normalize(owner):
                continue
            tgl = row[CNL_JADWAL_MAKEUP].strip() if len(row) > CNL_JADWAL_MAKEUP else ""
            if not tgl:
                continue
            kode = row[CNL_KODE].strip() if len(row) > CNL_KODE else ""
            if not kode:
                continue
            fasil = row[CNL_FASIL_MAKEUP].strip() if len(row) > CNL_FASIL_MAKEUP else ""
            out[kode.casefold()] = (tgl, fasil)
        return out

    async def get_makeup_notes(self, facilitator_name: str) -> dict[str, tuple[str, str]]:
        return await self._run(partial(self._fetch_makeup_notes, facilitator_name))

    async def get_all_loggable_classes(self, facilitator_name: str) -> tuple[list[ClassEntry], list[ClassEntry], list[ClassEntry]]:
        """Return (personal classes, backup classes, make-up classes)."""
        personal = await self.get_classes(facilitator_name)
        backup = await self.get_backup_classes(facilitator_name)
        makeup = await self.get_makeup_classes(facilitator_name)
        return personal, backup, makeup

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
        self._guard_grid(ws, ["J"], insert_row)
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
        # Kolom A "No." berisi nomor urut berurutan (1,2,3,...) — ikutkan di write.
        no = str(insert_row - 1)
        self._guard_grid(ws, ["I"], insert_row)
        ws.update(f"A{insert_row}:I{insert_row}", [[no] + row_data], value_input_option="USER_ENTERED")
        self._invalidate_rows(self.cfg.sheet_id, self.cfg.cancel_sheet)
        log.info("Wrote cancel %s/%s at row %d", rec.kode, rec.sesi, insert_row)
        return ws.row_count

    def _find_rekap_tab(self, facilitator_name: str) -> str:
        """Match registered name to per-fasil tab (nickname titles). Longest match wins."""
        target = self._normalize(facilitator_name)
        skip = {self._normalize(t) for t in _SYSTEM_TABS}
        best, best_len = None, 0
        for t in self._tabs(self.cfg.rekap_sheet_id):
            tn = self._normalize(t)  # normalize for compare only — keep RAW title
            if tn in skip:
                continue
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
        seps = self._separator_rows(self.cfg.rekap_sheet_id, tab)
        last_sep = seps[-1] if seps else -1
        if last_sep >= 0:
            # Separator garis hitam ada: kandidat = baris kosong PERTAMA (B..L
            # kosong) dengan i STRICTLY di bawah garis (i > last_sep). Gap/baris
            # kosong DI ATAS garis TIDAK dipakai — data baru di bawah garis.
            insert_idx = len(all_vals)
            for i, r in enumerate(all_vals):
                if i == 0 or i <= last_sep:
                    continue
                if not any((c or "").strip() for c in r[1:12]):
                    insert_idx = i
                    break
            insert_row = insert_idx + 1
        else:
            # Tak ada separator -> perilaku lama: baris kosong pertama sesudah header.
            insert_row = len(all_vals) + 1
            for i, r in enumerate(all_vals):
                if i == 0:
                    continue  # header
                if not any((c or "").strip() for c in r[1:12]):
                    insert_row = i + 1
                    break
        no = str(insert_row - 1)
        # Grid guard — "W" is the rightmost column we write (col 23).
        self._guard_grid(ws, ["W"], insert_row)
        ws.update(f"A{insert_row}:W{insert_row}", [[no] + row_data], value_input_option="USER_ENTERED")
        self._invalidate_rows(self.cfg.rekap_sheet_id, tab)
        _separator_cache.pop((self.cfg.rekap_sheet_id, tab), None)
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
        labels = ["Tanggal", "Dosen", "Jam", "Kode", "Matkul", "Pertemuan",
                  "SKS", "Tipe", "Sesi", "Peran", "Bukti"]
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
        return {"state": "complete", "count": len(rows), "row": rows[0][0]}

    async def rekap_row_status(self, tab: str, kode: str, tanggal_list: list) -> dict:
        return await self._run(partial(self._rekap_row_status, tab, kode, tanggal_list))

    def _rekap_row_values(self, tab: str, row_idx: int) -> list[str]:
        rows = self._cached_rows(self.cfg.rekap_sheet_id, tab)
        return rows[row_idx - 1] if 0 < row_idx <= len(rows) else []

    async def rekap_row_values(self, tab: str, row_idx: int) -> list[str]:
        return await self._run(partial(self._rekap_row_values, tab, row_idx))

    def _update_rekap_cells(self, tab: str, row_idx: int, cells: dict) -> None:
        if not cells:
            return
        ws = self._sheet_in(self.cfg.rekap_sheet_id, tab)
        data = [{"range": f"{_q(ws.title)}!{col}{row_idx}", "values": [[val]]}
                for col, val in cells.items() if val != ""]
        if data:
            self._guard_grid(ws, [col for col, val in cells.items() if val != ""], row_idx)
            ws.spreadsheet.values_batch_update(
                {"valueInputOption": "USER_ENTERED", "data": data})
            self._invalidate_rows(self.cfg.rekap_sheet_id, tab)
            _separator_cache.pop((self.cfg.rekap_sheet_id, tab), None)
        log.info("Updated rekap %s row %d cols %s", tab, row_idx, sorted(cells))

    async def update_rekap_cells(self, tab: str, row_idx: int, cells: dict) -> None:
        await self._run(partial(self._update_rekap_cells, tab, row_idx, cells))

    def _refresh_os(self, rekap_sheet_id: str, tab: str, row_idx: int, kode: str,
                    pertemuan_list, dosen: str) -> tuple[dict, dict, dict]:
        """Hitung ulang O..S baris rekap dari Absen+Feedback TERBARU — picker
        /rekap 🔃. HANYA kolom angka (total/hadir/feedback/tidak/belum) yang
        disentuh; B..L (termasuk Bukti L) tak pernah ditulis di sini. Tulis
        cuma sel yang nilainya BEDA via _update_rekap_cells (invalidate + grid
        guard + separator cache sudah ditangani di sana). Fail-open feedback:
        Q/S dilewati bila sheet Feedback gagal dibaca. Return (changed, before,
        after); changed = {col: (old, new)}. rekap_sheet_id diterima eksplisit
        utk walau helper lain pakai self.cfg.rekap_sheet_id."""
        nums = sorted({int(n) for n in re.findall(r"\d+", str(pertemuan_list or "")) if 1 <= int(n) <= 16})
        p_first = nums[0] if nums else 1
        counts = self._absen_counts(kode, p_first)
        total = counts["total"]
        fb = None
        try:
            fb = self._feedback_counts(kode, str(pertemuan_list or ""), dosen, counts.get("prodi", ""))
        except Exception:  # noqa: BLE001 — fail-open: feedback gak dibaca, Q/S tak disentuh
            fb = None
        if total:
            vals = {"O": str(total), "P": str(counts["hadir"]), "R": str(counts["tidak"])}
            if fb is not None:
                vals["Q"] = str(fb["q"])
                vals["S"] = str(max(total - fb["q"], 0))
        else:
            vals = {"O": "", "P": "", "Q": "", "R": "", "S": ""}
        cur = self._rekap_row_values(tab, row_idx)
        before, after, changed, write = {}, {}, {}, {}
        for col, nv in vals.items():
            idx = ord(col) - 65
            cv = cur[idx].strip() if len(cur) > idx else ""
            before[col], after[col] = cv, nv
            if nv and nv != cv:  # _update_rekap_cells menolak '' — jangan bilang berubah utk kosong
                changed[col] = (cv, nv)
                write[col] = nv
        if write:
            self._update_rekap_cells(tab, row_idx, write)
            _rekap_status_cache.clear()
        return changed, before, after

    async def refresh_os(self, rekap_sheet_id: str, tab: str, row_idx: int, kode: str,
                         pertemuan_list, dosen: str) -> tuple[dict, dict, dict]:
        return await self._run(partial(self._refresh_os, rekap_sheet_id, tab, row_idx, kode, pertemuan_list, dosen))

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

    def _feedback_candidates(self, kode: str, nums: set, d_toks: list) -> list[tuple[str, str, str, set]]:
        """(nim, school, blob, majors) utk tiap baris feedback lulus filter Q —
        SATU filter dipakai _feedback_counts DAN _feedback_nims (kode blob +
        pertemuan + dosen; kolom sama: kode r[8:25], pertemuan r[26],
        dosen r[25], school r[6], majors r[8:25:3]).
        NIM format hasil probe: mayoritas full 11 digit, kadang short/aneh —
        dibiarkan raw di sini, matching ke absen dilakukan _nim_match."""
        import time
        now = time.time()
        if _feedback_cache["rows"] is None or now - _feedback_cache["ts"] > 600:
            ss = self._client().open_by_key("1dZQcq3TvPh7wkW0z8SF94YExs5jONYf_O3oV09Hk604")
            _feedback_cache["rows"] = ss.worksheet("Form Responses 1").get_all_values()
            _feedback_cache["ts"] = now
        rows = _feedback_cache["rows"]
        kc = kode.strip().casefold()
        seen = set()
        out = []
        for r in rows[1:]:
            if len(r) <= 26:
                continue
            nim = r[2].strip() if len(r) > 2 else ""
            if not nim or nim in seen:
                continue
            blob = " ".join(c for c in r[8:25]).casefold()
            if kc not in blob:
                continue
            aa = {int(n) for n in re.findall(r"\d+", r[26].strip() if len(r) > 26 else "")}
            if nums and not (aa & nums):
                continue
            f_toks = self._norm_dosen(r[25] if len(r) > 25 else "")
            if d_toks and f_toks:
                if not (d_toks[-1] == f_toks[-1] and (d_toks[0] == f_toks[0] or len(d_toks) == 1 or len(f_toks) == 1)):
                    continue
            seen.add(nim)
            majors = {c.strip().casefold() for c in r[8:25:3] if c.strip()}
            out.append((nim, r[6].strip() if len(r) > 6 else "", blob, majors))
        return out

    def _nim_match(self, absen_nim: str, fb_nim: str) -> bool:
        """Match NIM absen ke NIM feedback. Hasil probe: keduanya full 11 digit
        (exact, 97% kasus); pengecualian nyata: feedback short '114' vs absen
        '...00114' — absen berakhiran fb (leading-zero style 001). Nama/
        NIM terpotong/13-digit TIDAK match."""
        a, b = (absen_nim or "").strip(), (fb_nim or "").strip()
        if not a or not b:
            return False
        if a == b:
            return True
        if a.isdigit() and b.isdigit() and 3 <= len(b) < len(a) and a.endswith(b):
            return True  # short-NIM '114' -> '26110100114'
        return False

    def _feedback_nims(self, kode: str, pertemuan_list: list[int], dosen: str) -> set[str]:
        """Set NIM feedback lulus filter Q utk pertemuan_list (bisa '3 dan 4')."""
        nums = {int(p) for p in pertemuan_list if 1 <= int(p) <= 16}
        return {nim for nim, _, _, _ in self._feedback_candidates(kode, nums, self._norm_dosen(dosen))}

    async def feedback_nims(self, kode: str, pertemuan_list: list[int], dosen: str) -> set[str]:
        return await self._run(partial(self._feedback_nims, kode, pertemuan_list, dosen))

    def _plan_convert_status(self, kode: str, pertemuan: int, nim_set: set[str]) -> list[dict]:
        """Baris absen (SEMUA blok kode di 15 tab) yg bakal diubah:
        sel == 'SF' & NIM di feedback -> 'S'; 'OF' -> 'O'.
        A/I/kosong/lain JANGAN disentuh. Dry-run: tanpa tulis.
        Short-NIM feedback ('001') hanya match kalau suffix-nya UNIK di roster
        (guard over-match: '001' bisa jadi ekor banyak NIM)."""
        if not 1 <= pertemuan <= 16:
            raise SheetsError("Pertemuan harus 1-16")
        blocks = self._locate_absen_block(kode)
        roster: list[tuple[str, str, str, int]] = []  # (nim, nama, title, row)
        for title, rows, nim_header in blocks:
            for r_idx in range(nim_header + 2, len(rows)):
                if rows[r_idx] and rows[r_idx][0].strip() == "Program Studi":
                    break
                nim = rows[r_idx][0].strip() if len(rows[r_idx]) > 0 else ""
                if not nim:
                    continue
                nama = rows[r_idx][1].strip() if len(rows[r_idx]) > 1 else ""
                roster.append((nim, nama or "-", title, r_idx + 1))
        # fb NIM yg cocok: exact dulu, lalu short-NIM suffix yg UNIK.
        roster_nims = [e[0] for e in roster]
        exact = {n for n in roster_nims} & nim_set
        match_nims = set(exact)
        for fb in nim_set:
            if fb in exact or not (fb.isdigit() and 3 <= len(fb) < 8):
                continue
            if sum(1 for n in roster_nims if n.endswith(fb)) == 1:
                match_nims.add(next(n for n in roster_nims if n.endswith(fb)))
        col_idx = 3 + (pertemuan - 1)
        out: list[dict] = []
        for nim, nama, title, row in roster:
            if nim not in match_nims:
                continue
            # read cell from cached rows (re-fetch block rows utk akses col)
            cols = self._all_absen_rows()[title]
            r0 = row - 1
            if r0 >= len(cols) or len(cols[r0]) <= col_idx:
                continue
            val = cols[r0][col_idx].strip().upper()
            if val not in ("SF", "OF"):
                continue
            out.append({"nim": nim, "nama": nama,
                        "dari": val, "ke": "S" if val == "SF" else "O",
                        "tab": title, "row": row})
        return out

    async def plan_convert_status(self, kode: str, pertemuan: int, nim_set: set[str]) -> list[dict]:
        return await self._run(partial(self._plan_convert_status, kode, pertemuan, nim_set))

    def _convert_status(self, kode: str, pertemuan: int, nim_set: set[str]) -> list[dict]:
        """Eksekusi _plan_convert_status: SATU values_batch_update utk semua blok,
        invalidate rows tiap tab + roster. Return daftar perubahan."""
        plan = self._plan_convert_status(kode, pertemuan, nim_set)
        if plan:
            col_letter = chr(ord("A") + 3 + (pertemuan - 1))
            data = [{"range": f"{_q(ch['tab'])}!{col_letter}{ch['row']}", "values": [[ch["ke"]]]}
                    for ch in plan]
            self._ss(self.cfg.absen_sheet_id).values_batch_update(
                {"valueInputOption": "USER_ENTERED", "data": data})
            for title in {ch["tab"] for ch in plan}:
                self._invalidate_rows(self.cfg.absen_sheet_id, title)
            self._invalidate_absen_students(kode)
        log.info("Convert absen %s pertemuan %d: %s",
                 kode, pertemuan, ",".join(f"{ch['nim']}:{ch['dari']}->{ch['ke']}" for ch in plan) or "-")
        return plan

    async def convert_status(self, kode: str, pertemuan: int, nim_set: set[str]) -> list[dict]:
        return await self._run(partial(self._convert_status, kode, pertemuan, nim_set))

    def _feedback_counts(self, kode: str, pertemuan: str, dosen: str, prodi_tab: str) -> dict:
        """Q = distinct NIM with clean match: kode + pertemuan + dosen + prodi + school."""
        nums = set(int(n) for n in re.findall(r"\d+", pertemuan or ""))
        school_exp = self._school_for_prodi(prodi_tab or "").casefold()
        pn = self._normalize(prodi_tab or "")
        seen, schools = set(), {}
        for nim, school, blob, majors in self._feedback_candidates(kode, nums, self._norm_dosen(dosen)):
            if nim in seen:
                continue
            if pn and not re.search(r"\b" + re.escape(pn) + r"\b", blob):
                # fallback: match any major cell equality
                if pn not in majors and not any(pn in m or m in pn for m in majors):
                    continue
            if school_exp:
                sch = school.casefold()
                if sch != school_exp:
                    continue
            seen.add(nim)
            schools[school] = schools.get(school, 0) + 1
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
                fields="files(id,name)", pageSize=100,
                supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", [])
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
        except googleapiclient.errors.HttpError as exc:
            reason = _api_reason(exc)
            raise SheetsError(f"Drive: {exc.resp.status} {exc.resp.reason}"
                              + (f" ({reason})" if reason else "")) from exc
        folder = drive.files().create(
            body={"name": facilitator, "mimeType": "application/vnd.google-apps.folder",
                  "parents": [parent]}, fields="id", supportsAllDrives=True).execute()
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
        from google_auth_httplib2 import AuthorizedHttp
        import httplib2
        import io
        creds = Credentials.from_service_account_file(str(self.cfg.service_account_json), scopes=SCOPES)
        # httplib2 timeout bounds every Drive request (incl. discovery fetch).
        drive = build("drive", "v3",
                      http=AuthorizedHttp(creds, http=httplib2.Http(timeout=30)))
        folder = self._bukti_folder_for(drive, facilitator.strip() or "Lainnya")
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mimetype or "image/jpeg")
        f = drive.files().create(body={"name": filename, "parents": [folder]},
                                 media_body=media, fields="id,webViewLink",
                                 supportsAllDrives=True).execute()
        try:
            drive.permissions().create(fileId=f["id"], body={"type": "anyone", "role": "reader"},
                                       supportsAllDrives=True).execute()
        except Exception:
            pass
        log.info("uploaded bukti %s -> folder %s", filename, folder)
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
        for title in (self.cfg.master_sheet, self.cfg.zoom_record_sheet, self.cfg.backup_sheet,
                      self.cfg.cancel_sheet):
            _rows_cache.pop((self.cfg.sheet_id, title), None)  # force refresh, ignore TTL
            self._cached_rows(self.cfg.sheet_id, title)

    async def _run(self, fn):
        loop = asyncio.get_running_loop()
        # Acquire the single-flight lock with a cap: a wedged holder (stuck
        # executor thread) must not queue every future Sheets call behind this
        # lock forever — that is the "DIAM TOTAL" failure. Timed out -> we did
        # NOT acquire, so nothing to release here.
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=90)
        except asyncio.TimeoutError as exc:
            log.error("sheets lock busy >90s — backing off")
            raise SheetsError("Google sibuk/antri penuh — coba lagi sebentar.") from exc
        try:
            try:
                # 60s wall-clock cap. Shield the executor future: on timeout the
                # thread keeps running, so we drain it *while still holding the
                # lock* — otherwise its late write races the next call.
                fut = loop.run_in_executor(None, fn)
                try:
                    return await asyncio.wait_for(asyncio.shield(fut), timeout=60)
                except asyncio.TimeoutError as exc:
                    log.exception("sheets call timed out")
                    try:
                        # Cap the drain too (30s): a truly wedged thread must
                        # not hold the lock forever. We let go; the late write
                        # may race the next call, but a silent total hang is
                        # worse — users get an error they can retry on.
                        await asyncio.wait_for(fut, timeout=30)
                    except BaseException:
                        pass  # drain: swallow CancelledError/anything from the kept-running thread
                    raise SheetsError("Google Sheets timeout (60 detik) — coba lagi.") from exc
            except SheetsError:
                raise
            except gspread.exceptions.GSpreadException as exc:
                log.exception("gspread failure")
                raise SheetsError(f"Gagal mengakses Google Sheets: {exc}") from exc
            except googleapiclient.errors.HttpError as exc:
                status = exc.resp.status
                log.exception("Drive/Sheets API failure (status %s)", status)
                hint = " — rate limit, coba lagi nanti." if status == 429 else ""
                reason = _api_reason(exc)
                raise SheetsError(f"Google API error {status}{hint}"
                                  + (f" — {reason}" if reason else "")) from exc
            except OSError as exc:
                log.exception("file/network failure")
                raise SheetsError("Gagal membaca kredensial/jaringan. Pastikan berkas kredensial tersedia.") from exc
            except Exception as exc:
                log.exception("unexpected sheets failure")
                raise SheetsError("Terjadi kesalahan saat mengakses Google Sheets. Coba lagi.") from exc
        finally:
            self._lock.release()


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
