"""Handler registration."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, ContextTypes

from config import Config
from handlers import absen, backup, cancel, darurat, heartbeat, log, rekap, reminder, schedule, start, stats
from handlers.register import register as register_user_cmd  # alias: name `register` below is the package-level fn

log_ = logging.getLogger(__name__)


async def on_error(update: Update | None, context: ContextTypes.DEFAULT_TYPE) -> None:
    log_.exception("Unhandled error", exc_info=context.error)
    if update and (msg := update.effective_message):
        try:
            await msg.reply_text("⚠️ Terjadi kesalahan internal. Coba lagi atau kirim /start.")
        except Exception:  # noqa: BLE001 - best-effort notify
            log_.warning("Could not deliver error message to chat %s", msg.chat_id)


def register(app: Application, cfg: Config) -> None:
    start.register(app, cfg)
    register_user_cmd(app, cfg)
    log.register(app, cfg)
    rekap.register(app, cfg)
    backup.register(app, cfg)
    cancel.register(app, cfg)
    absen.register(app, cfg)
    darurat.register(app, cfg)
    schedule.register(app, cfg)
    stats.register(app, cfg)
    reminder.register(app, cfg)
    heartbeat.register(app, cfg)
    app.add_error_handler(on_error)
