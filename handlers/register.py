"""/register <nama> — bind this Telegram chat to a facilitator name (multi-user)."""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import sheets
import users
from config import Config
from handlers import heartbeat, reminder

log = logging.getLogger(__name__)

NAME, = range(1)

USAGE = "Cara pakai: /register <nama>\nContoh: /register Adzril\n\nNama akan dicocokkan dengan data di sheet Jadwal Fasil."


async def _save_name(chat_id: int, full_name: str, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    prev = users.get(chat_id)
    users.registry().set(chat_id, full_name)
    await reminder.register_chat(chat_id, context)
    await heartbeat.register_chat(chat_id, context)
    log.info("Chat %s registered as %r (was %r)", chat_id, full_name, prev)
    return prev


async def _lookup_and_save(
    name: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE,
    reply_text=None, reply_edit=None,
) -> str:
    """Shared lookup logic. Returns 'saved' | 'need_pick' | 'not_found' | 'error'."""
    from telegram.constants import ChatAction
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass
    busy = None
    if reply_text:
        try:
            busy = await reply_text("⏳ Cari nama di Jadwal Fasil...")
        except Exception:
            busy = None
    try:
        matches = await context.bot_data["sheets"].find_facilitator_names(name)
    except sheets.SheetsError as exc:
        if busy is not None:
            try: await busy.delete()
            except Exception: pass
        if reply_text:
            await reply_text(f"⚠️ {exc}")
        return "error"
    if busy is not None:
        try: await busy.delete()
        except Exception: pass
    if not matches:
        if reply_text:
            await reply_text(f"❌ \"{name}\" tidak ditemukan di Jadwal Fasil.\nCoba dengan nama yang lebih lengkap.")
        return "not_found"
    if len(matches) == 1:
        full_name = matches[0]
        prev = await _save_name(chat_id, full_name, context)
        if reply_text:
            if prev and prev != full_name:
                await reply_text(f"✅ Nama diperbarui: {prev} → {full_name}")
            else:
                await reply_text(f"✅ Terdaftar sebagai: {full_name}")
            await reply_text("Kirim /zoom untuk mencatat kelas, /schedule untuk melihat jadwal.")
        return "saved"
    context.user_data["register_matches"] = matches
    rows = [[InlineKeyboardButton(m, callback_data=f"reg:{i}")] for i, m in enumerate(matches)]
    rows.append([InlineKeyboardButton("◀️ Kembali", callback_data="reg:back")])
    kb = InlineKeyboardMarkup(rows)
    if reply_text:
        await reply_text(
            f"🔍 Ditemukan {len(matches)} nama cocok dengan \"{name}\":\n\nPilih yang benar:",
            reply_markup=kb,
        )
    return "need_pick"


async def register_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = " ".join(context.args or []).strip()
    if name:
        await _lookup_and_save(
            name, update.effective_chat.id, context,
            reply_text=update.message.reply_text,
        )
        return ConversationHandler.END
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal", callback_data="reg:cancel")]])
    await update.message.reply_text("Ketik nama fasilitator kamu (contoh: Adzril):", reply_markup=kb)
    return NAME


async def register_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text(USAGE)
        return NAME
    res = await _lookup_and_save(
        name, update.effective_chat.id, context,
        reply_text=update.message.reply_text,
    )
    if res == "need_pick":
        return NAME
    return ConversationHandler.END


async def back_to_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal", callback_data="reg:cancel")]])
    await q.message.reply_text("Ketik nama fasilitator kamu (contoh: Adzril):", reply_markup=kb)
    return NAME


async def back_reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Dibatalkan.")
    return ConversationHandler.END


async def pick_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split(":")[1])
    matches = context.user_data.get("register_matches", [])
    if idx >= len(matches):
        return ConversationHandler.END
    full_name = matches[idx]
    chat_id = update.effective_chat.id
    prev = await _save_name(chat_id, full_name, context)
    if prev and prev != full_name:
        await q.message.edit_text(f"✅ Nama diperbarui: {prev} → {full_name}")
    else:
        await q.message.edit_text(f"✅ Terdaftar sebagai: {full_name}")
    await q.message.reply_text("Kirim /zoom untuk mencatat kelas, /schedule untuk melihat jadwal.")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Dibatalkan.")
    return ConversationHandler.END


def register(app: Application, cfg: Config) -> None:
    conv = ConversationHandler(
        entry_points=[CommandHandler("register", register_cmd)],
        states={
            NAME: [
                CallbackQueryHandler(pick_register, pattern=r"^reg:\d+$"),
                CallbackQueryHandler(back_to_name, pattern=r"^reg:back$"),
                CallbackQueryHandler(back_reg_cancel, pattern=r"^reg:cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, register_name),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=60 * 10,
        name="register_conv",
    )
    app.add_handler(conv)
