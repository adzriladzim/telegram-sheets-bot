"""/start and /help."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import Config
from handlers import heartbeat, reminder
import users

HELP = (
    "<b>Zoom Record Bot</b> — bot pencatat keseharian fasil di Cakrawala University.\n\n"
    "<b>Perintah:</b>\n"
    "• /register &lt;nama&gt; — set nama fasilitator\n"
    "• /zoom — isi form Zoom Record\n"
    "• /rekap — rekap kehadiran fasil\n"
    "• /absen — rekap kehadiran mahasiswa\n"
    "• /backup — izin & backup fasil\n"
    "• /cancel — lapor kelas cancel\n"
    "• /schedule — lihat jadwal minggu ini\n"
    "• /help — bantuan ini\n"
    "• /start — mulai ulang bot\n\n"
    "<b>Reminder:</b> pesan otomatis tiap jam {wib} WIB (pagi = jadwal penuh, siang/sore = kelas yang belum di-log) + notif bot aktif 05:00 WIB.\n\n"
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
    first = update.effective_user.first_name or "Kak"
    status = (
        f"Terdaftar sebagai: {facilitator}"
        if facilitator
        else "⚠️ Belum terdaftar.\nKetik /register <nama fasilitator> dulu, contoh:\n/register Adzril Adzim Hendrynov"
    )
    await update.message.reply_text(
        f"👋 Halo {first}!\n\n{status}\n\n"
        f"Aku bot pencatat keseharian fasil di Cakrawala University.\n"
        f"Tekan tombol di bawah atau kirim /zoom untuk mencatat kelas hari ini.\n\n"
        f"/help untuk daftar perintah.",
        reply_markup=_keyboard(),
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
