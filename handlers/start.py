"""/start and /help."""
from __future__ import annotations

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import Config
from handlers import heartbeat, reminder
import users

HELP = (
    "<b>TelefasilBot</b> — asisten harian fasil Cakrawala University.\n\n"
    "Semua urusan kelas dari chat ini: catat log pendampingan, absen, rekap, backup, "
    "sampai jadwal. 👇\n\n"
    "<b>Perintah:</b>\n"
    "• /register &lt;nama&gt; — daftar nama fasilitator (cukup sekali)\n"
    "• /zoom — catat log pendampingan kelas baru\n"
    "• /absen — rekap kehadiran mahasiswa\n"
    "• /rekap — rekap kehadiran fasil\n"
    "• /backup — izin & backup fasil\n"
    "• /cancel — lapor kelas cancel\n"
    "• /schedule — lihat jadwal minggu ini\n"
    "• /help — bantuan ini\n\n"
    "<b>Notif otomatis:</b> setiap jam {wib} WIB kami ingatkan — pagi jadwal "
    "penuh, siang/sore kelas yang belum di-log. Santai, tinggal follow.\n\n"
    "Pembuat: <b>Adzril Adzim</b>\n"
    "LinkedIn: <a href='https://linkedin.com/in/adzriladzim'>Adzril Adzim</a>\n"
    "Instagram: <a href='https://instagram.com/adzradzen07'>@adzradzen07</a>"
)


def _keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📝 Zoom", callback_data="go:log"), InlineKeyboardButton("🧾 Rekap", callback_data="go:rekap")],
         [InlineKeyboardButton("✅ Absen", callback_data="go:absen"), InlineKeyboardButton("🔄 Backup", callback_data="go:backup")],
         [InlineKeyboardButton("❌ Cancel", callback_data="go:cancel"), InlineKeyboardButton("🗓 Jadwal", callback_data="go:schedule")],
         [InlineKeyboardButton("ℹ️ Help", callback_data="go:help")]]
    )


async def go_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Hanya /help yang ditangani di sini — tombol lain langsung jadi
    # entry point ConversationHandler masing-masing (lihat register() tiap handler).
    q = update.callback_query
    await q.answer()
    await help_cmd(update, context)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from telegram.constants import ChatAction
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    except Exception:
        pass
    chat_id = update.effective_chat.id
    facilitator = users.get(chat_id)
    if facilitator:
        await reminder.register_chat(chat_id, context)  # (re)schedule this user's reminder job
        await heartbeat.register_chat(chat_id, context)  # (re)schedule daily 05:00 WIB heartbeat
    first = html.escape(update.effective_user.first_name or "Kak")
    status = (
        f"Terdaftar sebagai: {html.escape(facilitator)}"
        if facilitator
        else "⚠️ Belum terdaftar.\nKetik /register &lt;nama fasilitator&gt; dulu, contoh:\n/register Adzril Adzim Hendrynov"
    )
    await update.message.reply_text(
        f"👋 Halo {first}!\n\n"
        f"Aku <b>TelefasilBot</b> — asisten harian fasil Cakrawala: catat "
        f"log pendampingan, absen, rekap, backup, sampai jadwal, semua dari chat ini.\n\n"
        f"{status}\n\n"
        f"Tekan tombol di bawah atau kirim /zoom untuk mulai mencatat kelas "
        f"hari ini. 👇",
        reply_markup=_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["cfg"]
    wib = ", ".join(f"{(h + 7) % 24:02d}:{m:02d}" for h, m in cfg.reminder_slots)
    await context.bot.send_message(
        update.effective_chat.id,
        HELP.format(wib=wib),
        parse_mode=ParseMode.HTML,
    )


def register(app: Application, cfg: Config) -> None:
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(go_cb, pattern=r"^go:help$"))
