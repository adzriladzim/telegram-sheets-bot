"""Daily heartbeat — notif bot aktif jam 05:00 WIB ke semua user terdaftar.

Single broadcast job (bukan per-chat) supaya 17 kiriman jalan sekuensial
dengan retry + log, anti-timeout massal.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from datetime import time as dtime

from telegram.ext import Application, ContextTypes

import users
from config import Config

log = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7), name="WIB")
BROADCAST_NAME = "heartbeat:broadcast"

HEARTBEAT_TEXT = (
    "✅ <b>TelefasilBot aktif</b> — sistem normal.\n"
    "Ketik /zoom /absen /backup /cancel /schedule.\n"
    "Reminder kelas tetap jam 04:00 WIB bila ada jadwal hari ini."
)


def _log_line(text: str) -> None:
    try:
        from config import BASE_DIR
        p = BASE_DIR / "data" / "heartbeat.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S")
        with p.open("a", encoding="utf-8") as f:
            f.write(f"{ts} {text}\n")
    except Exception:
        pass


async def _schedule_job(app: Application) -> None:
    cfg: Config = app.bot_data["cfg"]
    if not cfg.heartbeat_enabled or app.job_queue is None:
        return
    # Bersihkan job heartbeat lama per-chat bila masih ada
    try:
        for job in list(app.job_queue.jobs()):
            if job.name == BROADCAST_NAME or (job.name or "").startswith("heartbeat:"):
                job.schedule_removal()
    except Exception:
        pass
    app.job_queue.run_daily(
        heartbeat_job,
        time=dtime(cfg.heartbeat_hour, cfg.heartbeat_minute, tzinfo=timezone.utc),
        name=BROADCAST_NAME,
    )


async def register_chat(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Compat: broadcast mencakup semua user, cukup pastikan job ada."""
    if not users.get(chat_id):
        return
    await _schedule_job(context.application)
    log.info("Heartbeat broadcast ensured for chat %s", chat_id)


async def heartbeat_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    ids = users.registered_chat_ids()
    ok, fail = 0, 0
    for cid in ids:
        try:
            await context.bot.send_message(cid, HEARTBEAT_TEXT, parse_mode="HTML")
            ok += 1
        except Exception:
            # retry sekali setelah jeda singkat
            try:
                await asyncio.sleep(2)
                await context.bot.send_message(cid, HEARTBEAT_TEXT, parse_mode="HTML")
                ok += 1
            except Exception as e2:
                fail += 1
                log.warning("Heartbeat failed for chat %s: %s", cid, e2)
        await asyncio.sleep(1)  # stagger biar tidak burst timeout
    _log_line(f"broadcast ok={ok} fail={fail} total={len(ids)}")
    log.info("Heartbeat broadcast done: ok=%d fail=%d total=%d", ok, fail, len(ids))


async def restore_jobs(app: Application) -> None:
    await _schedule_job(app)
    log.info("Heartbeat broadcast restored for %d registered user(s)", len(users.registered_chat_ids()))


def register(app: Application, cfg: Config) -> None:
    pass  # jobs derive from the users registry — see restore_jobs / register_chat
