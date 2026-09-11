"""Daily reminder jobs (JobQueue) — one per registered user (see users.py / data/users.json)."""
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


def _job_name(chat_id: int) -> str:
    return f"reminder:{chat_id}"


async def _schedule_job(app: Application, chat_id: int) -> None:
    cfg: Config = app.bot_data["cfg"]
    if not cfg.reminder_enabled or app.job_queue is None:
        return
    for job in app.job_queue.get_jobs_by_name(_job_name(chat_id)):
        job.schedule_removal()
    app.job_queue.run_daily(
        reminder_job,
        time=dtime(cfg.reminder_hour, cfg.reminder_minute, tzinfo=timezone.utc),
        name=_job_name(chat_id),
        chat_id=chat_id,
    )


async def register_chat(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ensure a reminder job exists for a registered user."""
    if not users.get(chat_id):
        return  # not registered — nothing to remind
    await _schedule_job(context.application, chat_id)
    log.info("Reminder job scheduled for chat %s", chat_id)


async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if (chat_id := context.job.chat_id if context.job else None) is None:
        return
    facilitator = users.get(chat_id)
    if not facilitator:
        log.debug("Reminder skipped for %s: not registered", chat_id)
        return
    try:
        classes = await context.bot_data["sheets"].get_classes(facilitator)
    except sheets.SheetsError as exc:
        log.warning("Reminder skipped for %s (%s): %s", chat_id, facilitator, exc)
        return
    today = sheets.today_day_wib()
    mine = [c for c in classes if _norm_day(c.day) == _norm_day(today)]
    if not mine:
        log.debug("No class today (%s) for %s / %s", today, facilitator, chat_id)
        return
    lines = [f"⏰ <b>Pengingat — ada {len(mine)} kelas hari ini ({today}):</b>", ""]
    for c in mine:
        lines.append(
            f"• <b>{html.escape(c.time_range)}</b> {html.escape(c.code)} — {html.escape(c.subject)}\n"
            f"  🏫 {html.escape(c.room)} | {html.escape(c.zoom_label)}\n"
            f"  Jangan lupa isi /log setelah kelas ya!"
        )
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
