"""/H+1 Follow-up Zoom Record — job harian broadcast per fasil terdaftar.

Slot default 00:00 UTC = 07:00 WIB (env H1_FOLLOWUP_HOUR, UTC). TERPISAH dari
reminder_slots: semantics `_is_full_slot` (04/12/20 WIB) tidak disentuh.

Deteksi: kelas KEMARIN (WIB) yang belum di-log di Zoom Record.
- Personal: yesterday.weekday() == DAY_ORDER[c.day] (hari KEMARIN, bukan
  last_date_for_day yang bisa include today).
- Backup/make-up: parse_backup_date(backup_hari_tanggal) == yesterday.
- SKIP: sudah di-log (get_done_by_date — sudah termasuk delegasi yg direkam
  pengganti) ATAU delegasi backup aktif utk fasil tsb (active_backup_keys:
  fasil awal col B dianggap done, fix ead798d).
- TIDAK filter minggu berjalan utk backup: kemarin bisa lintas week boundary
  (mis. tadi Minggu, hari ini Senin) — pakai anchor tanggal eksplisit.

Aksi tombol → conversation EXISTING (cancel/backup/log), prefill kode via
callback-data; guard `_guard` menolak prefill kalau conv lain aktif.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import date, datetime, timedelta
from datetime import time as dtime
from datetime import timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

import sheets
import usage
import users
from config import Config

log = logging.getLogger(__name__)

BROADCAST_NAME = "h1_followup:broadcast"

# Callback data (prefix — kode class menempel setelah titik dua pertama):
#   h1c:<kode>  ❌ Dibatalkan  -> /cancel (prefill)
#   h1r:<kode>  🔁 Reschedule  -> /cancel (prefill + catatan "reschedule")
#   h1b:<kode>  🔄 Backup fasil -> /backup (prefill)
#   h1l:<kode>  ✏️ Isi /zoom    -> /zoom  (prefill)
#   h1s:<kode>  ✅ Udah, cek sendiri -> /zoom (prefill, sama dgn h1l)
#   h1z:check   tombol global per pesan
P_CNL = "h1c:"
P_RSD = "h1r:"
P_BAK = "h1b:"
P_LOG = "h1l:"
P_CHK = "h1s:"

# Max kelas per pesan (spec: gabung max ~5 per pesan; sisanya batch baru).
_BATCH = 5


# ---------- date anchor ----------

def _yesterday_wib(now=None) -> tuple[date, str]:
    """(date, 'dd/mm/yyyy') kemarin WIB. `now` (datetime WIB-aware) hanya utk test."""
    t = (now or datetime.now(sheets.WIB)).date()
    y = t - timedelta(days=1)
    return y, y.strftime("%d/%m/%Y")


def _norm_day(v: str) -> str:
    return "".join(ch for ch in v.lower() if ch.isalpha())


def _day_index(v: str) -> int | None:
    norm = _norm_day(v)
    return next((i for i, d in enumerate(sheets.DAY_ORDER) if _norm_day(d) == norm), None)


def _class_on_date(c: sheets.ClassEntry, ydate: date, ystr: str) -> bool:
    """Kelas `c` jatuh pada tanggal kemarin?
    Personal: day-name match dgn yesterday.weekday() (HARI KEMARIN, bukan
    last_date_for_day yg include today). Backup/make-up: tanggal eksplisit."""
    if c.category in ("Backup", "Make-up") and c.backup_hari_tanggal:
        # parse_backup_date fallback-ke-today saat tak terparse — mustahil sama
        # dgn kemarin (ystr != today), jadi tak pernah false-positive.
        return sheets.parse_backup_date(c.backup_hari_tanggal) == ystr
    idx = _day_index(c.day)
    return idx is not None and idx == ydate.weekday()


def _class_key(c: sheets.ClassEntry, ystr: str) -> tuple[str, str]:
    return (c.code.casefold(), ystr)


# ---------- detection ----------

async def _detect_missing(sheets_client, facilitator: str, now=None) -> list[sheets.ClassEntry]:
    """Kelas kemarin yg BELUM di-log utk satu fasil. `now` hanya utk test."""
    ydate, ystr = _yesterday_wib(now)
    personal = await sheets_client.get_classes(facilitator)
    # Backup: anchor tanggal EKSPLISIT (bisa lintas week-window today).
    backup = await sheets_client.backup_classes_on(facilitator, ydate)
    # Make-up: get_makeup_classes tanpa filter minggu — saring di _class_on_date.
    makeup = await sheets_client.get_makeup_classes(facilitator)
    try:
        done = await sheets_client.get_done_by_date(facilitator)
    except sheets.SheetsError as exc:
        log.warning("H+1 done-map failed for %s: %s", facilitator, exc)
        done = set()  # fail-open spt reminder._filter_todo
    try:
        delegated = await sheets_client.active_backup_keys(facilitator)
    except sheets.SheetsError as exc:
        log.warning("H+1 delegated-map failed for %s: %s", facilitator, exc)
        delegated = set()
    missing = []
    for c in personal + backup + makeup:
        if not _class_on_date(c, ydate, ystr):
            continue
        key = _class_key(c, ystr)
        if key in done or key in delegated:
            continue
        missing.append(c)
    missing.sort(key=lambda c: c.start_time)
    return missing


# ---------- message ----------

def _chunk(classes: list[sheets.ClassEntry], size: int = _BATCH) -> list[list[sheets.ClassEntry]]:
    return [classes[i:i + size] for i in range(0, len(classes), size)]


def _batch_text(ydate: date, ystr: str, batch: list[sheets.ClassEntry]) -> str:
    day_name = sheets.DAY_ORDER[ydate.weekday()]
    lines = [
        "📩 <b>H+1 — kelas kemarin "
        f"({html.escape(day_name)}, {html.escape(sheets.tanggal_panjang(ystr))}) "
        "belum ada Zoom Record:</b>"
    ]
    for c in batch:
        lines.append(
            f"📩 H+1 — Kelas {html.escape(c.code)} ({html.escape(c.time_range)}) "
            f"kemarin belum ada Zoom Record. Kenapa?\n"
            f"   {html.escape(c.subject)}"
        )
    return "\n".join(lines)


def _batch_kb(batch: list[sheets.ClassEntry]) -> InlineKeyboardMarkup:
    rows = []
    for c in batch:
        rows.append([
            InlineKeyboardButton("❌ Dibatalkan", callback_data=f"{P_CNL}{c.code}"),
            InlineKeyboardButton("🔁 Reschedule", callback_data=f"{P_RSD}{c.code}"),
            InlineKeyboardButton("🔄 Backup fasil", callback_data=f"{P_BAK}{c.code}"),
            InlineKeyboardButton("✏️ Isi /zoom", callback_data=f"{P_LOG}{c.code}"),
        ])
    rows.append([InlineKeyboardButton("✅ Udah, cek sendiri", callback_data="h1z:check")])
    return InlineKeyboardMarkup(rows)


# ---------- aksi tombol non-conversation ----------

async def cek_sendiri(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tombol global per pesan: user bilang udah cek — cukup hint /zoom."""
    q = update.callback_query
    await q.answer()
    try:
        await q.message.reply_text(
            "Oke! Kalau ternyata masih ada yang belum tercatat, langsung /zoom ya. 😉")
    except Exception:
        pass


# ---------- job ----------

async def h1_followup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    sheets_client = context.bot_data["sheets"]
    sent = 0
    chats_notified = 0
    for cid in users.registered_chat_ids():
        facilitator = users.get(cid)
        if not facilitator:
            continue
        try:
            missing = await _detect_missing(sheets_client, facilitator)
        except sheets.SheetsError as exc:
            log.warning("H+1 skipped for %s (%s): %s", cid, facilitator, exc)
            continue
        if not missing:
            continue
        ydate, ystr = _yesterday_wib()
        notified = False
        for batch in _chunk(missing):
            try:
                await context.bot.send_message(
                    cid, _batch_text(ydate, ystr, batch),
                    parse_mode="HTML", reply_markup=_batch_kb(batch))
            except Exception as exc:
                log.warning("H+1 send failed for %s: %s", cid, exc)
                continue
            if not notified:
                notified = True
                chats_notified += 1
            for c in batch:
                usage.log(cid, facilitator, "h1_followup", c.code)
            sent += len(batch)
            await asyncio.sleep(1)  # stagger per-batch — hindari burst 429 antar batch
    log.info("H+1 follow-up done: %d kelas diingatkan utk %d chat",
             sent, chats_notified)


# ---------- scheduling ----------

async def _schedule_job(app: Application) -> None:
    cfg: Config = app.bot_data["cfg"]
    if not cfg.h1_followup_enabled or app.job_queue is None:
        return
    try:
        for job in list(app.job_queue.jobs()):
            if job.name == BROADCAST_NAME:
                job.schedule_removal()
    except Exception:
        pass
    app.job_queue.run_daily(
        h1_followup_job,
        time=dtime(cfg.h1_followup_hour, 0, tzinfo=timezone.utc),
        name=BROADCAST_NAME,
    )


async def register_chat(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Compat dgn heartbeat: broadcast mencakup semua user, cukup pastikan job ada."""
    if not users.get(chat_id):
        return
    await _schedule_job(context.application)
    log.info("H+1 follow-up broadcast ensured for chat %s", chat_id)


async def restore_jobs(app: Application) -> None:
    await _schedule_job(app)
    log.info("H+1 follow-up restored for %d registered user(s)", len(users.registered_chat_ids()))


def register(app: Application, cfg: Config) -> None:
    app.add_handler(CallbackQueryHandler(cek_sendiri, pattern=r"^h1z:check$"))