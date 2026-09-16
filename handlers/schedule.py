"""/schedule — this week's classes from the master sheet."""
from __future__ import annotations

import html
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import sheets
import users
from config import Config
from handlers import status as st

log = logging.getLogger(__name__)


async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.callback_query:
        await update.callback_query.answer()
    facilitator = users.get(update.effective_chat.id)
    if not facilitator:
        await update.effective_message.reply_text(users.UNREGISTERED_MSG)
        return
    busy = await st.loading(update, context, "⏳ Ambil jadwal dari sheet...")
    try:
        personal, backup = await context.bot_data["sheets"].get_all_loggable_classes(facilitator)
    except sheets.SheetsError as exc:
        await st.unbusy(busy)
        await update.effective_message.reply_text(f"⚠️ {exc}")
        return
    await st.unbusy(busy)
    classes = personal + backup
    if not classes:
        await update.effective_message.reply_text(f"Tidak ada kelas terdaftar untuk {facilitator} (jadwal + backup kosong).")
        return
    by_day = sheets.this_week_classes(classes)
    today = sheets.today_day_wib()
    today_full = sheets.today_str_wib()
    from handlers.log import _parse_backup_date
    lines = ["🗓 <b>Jadwal Kelas Minggu Ini</b>", ""]
    for day in sheets.DAY_ORDER:
        for c in by_day.get(day, []):
            if c.category == "Backup":
                mark = " ← <b>HARI INI</b>" if _parse_backup_date(c.backup_hari_tanggal) == today_full else ""
                lines.append(
                    f"<b>{day}</b>🔄 {html.escape(c.code)} — {html.escape(c.subject)} ({html.escape(str(c.backup_hari_tanggal))}){mark}\n"
                    f"  🏫 {html.escape(c.room)} | 👤 {html.escape(c.lecturer)} | {html.escape(c.zoom_label)}"
                )
            else:
                mark = " ← <b>HARI INI</b>" if day == today else ""
                lines.append(
                    f"<b>{day}</b> {html.escape(c.time_range)}{mark}\n"
                    f"  {html.escape(c.code)} — {html.escape(c.subject)}\n"
                    f"  🏫 {html.escape(c.room)} | 👤 {html.escape(c.lecturer)} | {html.escape(c.zoom_label)}"
                )
            lines.append("")
    missing = [c.code for c in classes if not sheets.this_week_classes([c])]
    if missing:
        lines.append(f"⚠️ Hari tak dikenali (cek sheet): {', '.join(html.escape(x) for x in missing)}")
    await update.effective_message.reply_text("\n".join(lines).strip(), parse_mode=ParseMode.HTML)


def register(app: Application, cfg: Config) -> None:
    app.add_handler(CommandHandler("schedule", schedule_cmd))
    app.add_handler(CallbackQueryHandler(schedule_cmd, pattern=r"^go:schedule$"))
