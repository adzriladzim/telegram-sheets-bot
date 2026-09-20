"""Environment config. Single source of truth for env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class ConfigError(RuntimeError):
    pass


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _int_list(name: str, default: str, sep: str = ",") -> tuple[int, ...]:
    """Comma-separated ints, e.g. REMINDER_TIMES='21,5,13' -> (21, 5, 13)."""
    raw = os.getenv(name, default).strip()
    out = []
    for part in raw.split(sep):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError as exc:
            raise ConfigError(f"{name} must be comma-separated integers, got {raw!r}") from exc
    return tuple(out)


def _reminder_slots() -> tuple[tuple[int, int], ...]:
    """(UTC hour, minute) reminder slots. Legacy REMINDER_HOUR/MINUTE = single slot."""
    legacy_hour = os.getenv("REMINDER_HOUR", "").strip()
    if legacy_hour:
        return ((_int("REMINDER_HOUR", 11), _int("REMINDER_MINUTE", 0)),)
    return tuple((h, 0) for h in _int_list("REMINDER_TIMES", "21,5,13"))


@dataclass(frozen=True)
class Config:
    bot_token: str
    sheet_id: str
    service_account_json: Path
    facilitator_name: str
    master_sheet: str
    zoom_record_sheet: str
    backup_sheet: str
    cancel_sheet: str
    absen_sheet_id: str
    absen_sheet_name: str
    rekap_sheet_id: str
    rekap_bukti_folder_id: str
    semester: str
    # (UTC hour, minute) reminder slots: 21,5,13 UTC = 04:00,12:00,20:00 WIB.
    # Slot paling pagi WIB = pengingat jadwal penuh; slot lain = kelas belum di-log.
    reminder_slots: tuple[tuple[int, int], ...]
    reminder_enabled: bool
    heartbeat_hour: int  # UTC
    heartbeat_minute: int
    heartbeat_enabled: bool
    darurat_online: bool = False

    # Tab Tukar Jadwal (swap fasil, tanpa approval) — dibuat otomatis bila belum ada.
    tukar_sheet: str = "Tukar Jadwal"

    # Column layout of the Zoom Record sheet (B..O -> index 1..14)
    # Batch 5 (Ganjil 26/27): Kelas(F), Mata Kuliah(G) — swapped vs old batch
    record_columns: tuple[str, ...] = field(
        default=(
            "tanggal_pengisian",  # B
            "nama_fasilitator",   # C
            "tanggal_perkuliahan",  # D
            "semester",           # E
            "kelas",              # F
            "mata_kuliah",        # G
            "pertemuan",          # H
            "skema",              # I
            "sks",                # J
            "tipe_kelas",         # K
            "dosen",              # L
            "jam_mulai",          # M
            "zoom",               # N
            "catatan",            # O
        )
    )


def _service_account_path() -> Path:
    """Railway/cloud: isi GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT dengan isi file JSON
    (tanpa upload file). Ditulis ke secrets/service_account.json saat start."""
    p = BASE_DIR / os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json").strip()
    content = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT", "").strip()
    if content and not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return p


def load_config() -> Config:
    cfg = Config(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        sheet_id=os.getenv("GOOGLE_SHEET_ID", "1Y5wFTBj_04tkpCd0r0ySGwgNDBO7hQNGdwJKwJM5DIM").strip(),
        # Sheet-id defaults = Batch 5 Ganjil 26/27 (fungsional, bukan secret).
        # Env override dari .env/Railway; isi nyata user ada di .env, bukan di sini.
        service_account_json=_service_account_path(),
        # Optional: legacy single-user default. Per-user names live in data/users.json (/register).
        facilitator_name=os.getenv("FACILITATOR_NAME", "").strip(),
        master_sheet=os.getenv("MASTER_SHEET_NAME", "Jadwal Fasil Blok A Ganjil 26/27").strip(),
        zoom_record_sheet=os.getenv("ZOOM_RECORD_SHEET_NAME", "Zoom Record Blok A Ganjil 26/27").strip(),
        backup_sheet=os.getenv("BACKUP_SHEET_NAME", "Backup Fasilitator Blok A Ganjil 26/27").strip(),
        cancel_sheet=os.getenv("CANCEL_SHEET_NAME", "Kelas Cancel & Pengganti Blok A Ganjil 26/27").strip(),
        tukar_sheet=os.getenv("TUKAR_SHEET_NAME", "Tukar Jadwal").strip(),
        absen_sheet_id=os.getenv("ABSEN_SHEET_ID", "1QA2K2HKBNQzt9VVnZrOv7SeyAcOonEWfejVoGmfkBEg").strip(),
        absen_sheet_name=os.getenv("ABSEN_SHEET_NAME", "Computer Science").strip(),
        rekap_sheet_id=os.getenv("REKAP_SHEET_ID", "1FUK-c1AzTscfXZLQETpGbiyyVCw38n-8FnvYi-lD3Rw").strip(),
        rekap_bukti_folder_id=os.getenv("REKAP_BUKTI_FOLDER_ID", "").strip(),
        semester=os.getenv("SEMESTER", "1").strip(),
        reminder_slots=_reminder_slots(),
        reminder_enabled=os.getenv("REMINDER_ENABLED", "true").strip().lower() in {"1", "true", "yes"},
        heartbeat_hour=_int("HEARTBEAT_HOUR", 22),
        heartbeat_minute=_int("HEARTBEAT_MINUTE", 0),
        heartbeat_enabled=os.getenv("HEARTBEAT_ENABLED", "true").strip().lower() in {"1", "true", "yes"},
        darurat_online=os.getenv("DARURAT_ONLINE", "false").strip().lower() in {"1", "true", "yes"},
    )
    if not cfg.bot_token:
        raise ConfigError("TELEGRAM_BOT_TOKEN is empty. Copy .env.example to .env and fill it.")
    if not cfg.service_account_json.exists():
        raise ConfigError(f"Service account file not found: {cfg.service_account_json}")
    return cfg
