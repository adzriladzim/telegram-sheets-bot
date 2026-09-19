"""Zoom Record entry bot — Telegram ⇄ Google Sheets.

Run: python bot.py   (after .env + service_account.json are in place)
"""
from __future__ import annotations

import logging
import sys

from telegram import BotCommand
from telegram.ext import Application, ContextTypes

import config
import sheets
import users
from config import Config
from handlers import heartbeat, reminder
from handlers import register as register_handlers

BASE_DIR = config.BASE_DIR


def _setup_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        level=logging.INFO,
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per API call otherwise


async def _sheets_warm_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic cache flush (re-seed): keep master/zoom/backup rows fresh."""
    await context.bot_data["sheets"].warmup()


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Mulai bot"),
            BotCommand("register", "Set nama fasilitator"),
            BotCommand("zoom", "Isi form Zoom Record"),
            BotCommand("rekap", "Rekap kehadiran fasil"),
            BotCommand("backup", "Izin & backup fasil"),
            BotCommand("cancel", "Lapor kelas cancel"),
            BotCommand("absen", "Rekap kehadiran mahasiswa"),
            BotCommand("sinkron", "Naikkan SF/OF jadi S/O (sudah isi feedback)"),
            BotCommand("stats", "Stats admin"),
            BotCommand("schedule", "Jadwal minggu ini"),
            BotCommand("help", "Bantuan"),
        ]
    )
    # Seed whole-sheet caches at boot + periodic refresh (see sheets.py warmup).
    await app.bot_data["sheets"].warmup()
    if app.job_queue is not None:
        app.job_queue.run_repeating(_sheets_warm_job, interval=1800, first=1800, name="sheets:warm")
    await reminder.restore_jobs(app)
    await heartbeat.restore_jobs(app)
    logging.getLogger(__name__).info("Bot ready.")


def main() -> None:
    _setup_logging()
    log = logging.getLogger("bot")
    try:
        cfg = config.load_config()
    except config.ConfigError as exc:
        log.error("Config: %s", exc)
        sys.exit(1)

    # Multi-user registry; FACILITATOR_NAME (if set) seeds legacy chats from data/chats.json.
    users.init(BASE_DIR / "data" / "users.json", default_name=cfg.facilitator_name)

    app = (
        Application.builder()
        .token(cfg.bot_token)
        .post_init(_post_init)
        .connect_timeout(10)
        .read_timeout(20)
        # Scale-out: process updates from different chats concurrently. Same-chat
        # gspread-write handlers are serialized per-chat (sheets.SheetsClient.for_chat)
        # so ConversationHandler stays reliable + no double-write on rapid taps.
        .concurrent_updates(True)
        .build()
    )
    app.bot_data["cfg"] = cfg
    app.bot_data["base_dir"] = BASE_DIR
    app.bot_data["sheets"] = sheets.SheetsClient(cfg)

    register_handlers(app, cfg)
    log.info("Starting polling (%d registered user(s), default facilitator=%r)",
             len(users.registered_chat_ids()), cfg.facilitator_name)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
