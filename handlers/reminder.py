"""Daily reminder jobs (JobQueue) — one job per UTC slot per registered user (see users.py / data/users.json).

Slot paling pagi WIB (default 21 UTC = 04:00 WIB) kirim jadwal penuh hari ini;
slot lain (12:00/20:00 WIB) hanya kelas yang belum di-log di Zoom Record.
"""
from __future__ import annotations

import html
import logging
from datetime import time as dtime
from datetime import timezone

from telegram.ext import Application, ContextTypes

import sheets
import users
from config import Config

log = logging.getLogger(__name__)


def _job_name(chat_id: int, hour: int, minute: int) -> str:
    return f"reminder:{chat_id}:{hour:02d}{minute:02d}"


def _is_full_slot(cfg: Config, job_name: str) -> bool:
    """Slot dengan jam WIB paling pagi = kirim jadwal penuh; sisanya filter done-map."""
    hours = [h for h, _ in cfg.reminder_slots]
    try:
        hour = int(job_name.rsplit(":", 1)[-1][:2])
    except (ValueError, IndexError):
        return True  # job name tak dikenal → aman: kirim jadwal penuh
    if not hours:
        return True
    earliest_wib = min((h + 7) % 24 for h in hours)
    return (hour + 7) % 24 == earliest_wib


async def _schedule_job(app: Application, chat_id: int) -> None:
    cfg: Config = app.bot_data["cfg"]
    if not cfg.reminder_enabled or app.job_queue is None:
        return
    for hour, minute in cfg.reminder_slots:
        name = _job_name(chat_id, hour, minute)
        for job in app.job_queue.get_jobs_by_name(name):
            job.schedule_removal()
        app.job_queue.run_daily(
            reminder_job,
            time=dtime(hour, minute, tzinfo=timezone.utc),
            name=name,
            chat_id=chat_id,
        )


async def register_chat(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ensure reminder jobs exist for a registered user (all slots)."""
    if not users.get(chat_id):
        return  # not registered — nothing to remind
    await _schedule_job(context.application, chat_id)
    log.info("Reminder jobs scheduled for chat %s", chat_id)


async def _filter_todo(
    sheets_client, facilitator: str, mine: list[sheets.ClassEntry]
) -> list[sheets.ClassEntry]:
    """Hanya kelas hari ini yang BELUM ada di Zoom Record (done-by-date)."""
    try:
        done = await sheets_client.get_done_by_date(facilitator)
    except sheets.SheetsError as exc:
        log.warning("Done-map failed for %s: %s", facilitator, exc)
        return mine  # fail-open: kirim semua, jangan sampai kelas terlewat

    now = sheets.today_str_wib()
    todo = []
    for c in mine:
        if not sheets.backup_date_in_week(c):
            continue  # backup/make-up basi lintas minggu — jangan remind
        if c.category in ("Backup", "Make-up") and c.backup_hari_tanggal:
            tgl = sheets.parse_backup_date(c.backup_hari_tanggal)
        else:
            tgl = sheets.last_date_for_day(c.day)
        if (c.code.casefold(), tgl) not in done:
            todo.append(c)
    return todo


async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if (chat_id := context.job.chat_id if context.job else None) is None:
        return
    facilitator = users.get(chat_id)
    if not facilitator:
        log.debug("Reminder skipped for %s: not registered", chat_id)
        return
    cfg: Config = context.bot_data["cfg"]
    full_slot = _is_full_slot(cfg, context.job.name)
    try:
        personal, backup, makeup = await context.bot_data["sheets"].get_all_loggable_classes(facilitator)
    except sheets.SheetsError as exc:
        log.warning("Reminder skipped for %s (%s): %s", chat_id, facilitator, exc)
        return
    classes = personal + backup + makeup
    today = sheets.today_day_wib()
    # Window minggu dulu (backup/make-up basi jangan match day-name), baru day match.
    classes = [c for c in classes if sheets.backup_date_in_week(c)]
    mine = [c for c in classes if _norm_day(c.day) == _norm_day(today)]
    if not mine:
        log.debug("No class today (%s) for %s / %s", today, facilitator, chat_id)
        return
    if not full_slot:
        mine = await _filter_todo(context.bot_data["sheets"], facilitator, mine)
        if not mine:
            log.info("Reminder skipped for %s: semua kelas %s sudah di-log", chat_id, today)
            return  # hemat spam
    if full_slot:
        head = f"⏰ <b>Pengingat — ada {len(mine)} kelas hari ini ({today}):</b>"
    else:
        head = f"⏰ <b>Pengingat — {len(mine)} kelas hari ini ({today}) belum di-log:</b>"
    lines = [head, ""]
    for c in mine:
        lines.append(
            f"• <b>{html.escape(c.time_range)}</b> {html.escape(c.code)} — {html.escape(c.subject)}\n"
            f"  🏫 {html.escape(c.room)} | {html.escape(c.zoom_label)}\n"
            f"  Jangan lupa isi /log setelah kelas ya!"
        )
    if not full_slot:
        lines += ["", "Jangan lupa /absen + /rekap!"]
    await context.bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")


def _norm_day(v: str) -> str:
    return "".join(ch for ch in v.lower() if ch.isalpha())


async def restore_jobs(app: Application) -> None:
    ids = users.registered_chat_ids()
    for cid in ids:
        await _schedule_job(app, cid)
    log.info("Reminder jobs restored: %d registered user(s)", len(ids))


def register(app: Application, cfg: Config) -> None:
    pass  # jobs derive from the users registry — see restore_jobs / register_chat