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
from handlers import _guard

log = logging.getLogger(__name__)

NAME, = range(1)

# Set di register() — referensi conv utk guard re-entry + cross-handler (S2).
REGISTER_CONV = None

USAGE = "Cara pakai: /register <nama>\nContoh: /register Adzril\n\nNama akan dicocokkan dengan data di sheet Jadwal Fasil."

# user_data keys (global per-chat, dipakai juga DI LUAR ConversationHandler)
WAITING = "register_waiting_name"   # True saat conversation lagi menunggu ketik nama
RGN_MATCHES = "rgn_matches"         # daftar match utk tombol pilih non-conversation (rgn:{i})
REG_JUST = "reg_just_saw_text"      # nama yg BARU diproses group 0 — cegah duplikat di group 1


async def _save_name(chat_id: int, full_name: str, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    prev = users.get(chat_id)
    users.registry().set(chat_id, full_name)
    await reminder.register_chat(chat_id, context)
    await heartbeat.register_chat(chat_id, context)
    log.info("Chat %s registered as %r (was %r)", chat_id, full_name, prev)
    return prev


async def _lookup_and_save(
    name: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE,
    reply_text=None,
    outside_conversation: bool = False,
) -> str:
    """Shared lookup+register logic. Returns 'saved' | 'need_pick' | 'not_found' | 'error'.

    outside_conversation=True → dipakai text_unregistered (tanpa /register): tombol
    pilih pakai data "rgn:{i}" + matches disimpan di user_data global, tanpa tombol
    kembali (tidak ada ConversationHandler yang menangani).
    """
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
            await reply_text(
                f"❌ \"{name}\" tidak ditemukan di Jadwal Fasil.\n"
                "Coba dengan nama yang lebih lengkap, contoh: Adzril Adzim Hendrynov."
            )
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
    if outside_conversation:
        context.user_data[RGN_MATCHES] = matches
        rows = [[InlineKeyboardButton(m, callback_data=f"rgn:{i}")] for i, m in enumerate(matches)]
    else:
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
    # S2 guard: re-entry form yg sama -> jangan reset; handler lain aktif -> blok.
    g = await _guard.guard_entry(update, context, "register_conv", "register")
    if g is not None:
        return g
    name = " ".join(context.args or []).strip()
    if name:
        res = await _lookup_and_save(
            name, update.effective_chat.id, context,
            reply_text=update.message.reply_text,
        )
        if res == "need_pick":
            context.user_data[WAITING] = True  # conversation stay active utk tombol reg:
            return NAME
        return ConversationHandler.END
    context.user_data[WAITING] = True  # conversation aktif: tunggu ketik nama
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
    context.user_data[REG_JUST] = name  # dipakai group 1 utk hindari duplikat lookup
    context.user_data.pop(WAITING, None)  # conversation selesai
    return ConversationHandler.END


async def back_to_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    context.user_data[WAITING] = True
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal", callback_data="reg:cancel")]])
    await q.message.reply_text("Ketik nama fasilitator kamu (contoh: Adzril):", reply_markup=kb)
    return NAME


async def back_reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    context.user_data.pop(WAITING, None)
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
    context.user_data.pop(WAITING, None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(WAITING, None)
    await update.message.reply_text("Dibatalkan.")
    return ConversationHandler.END


async def reg_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Conversation kehabisan waktu (conversation_timeout) — bersihkan flag WAITING."""
    context.user_data.pop(WAITING, None)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⏰ Sesi daftar nama kedaluwarsa. Ketik /register lagi kalau mau lanjut."
            )
        except Exception:
            pass
    return ConversationHandler.END


# ---------- jalur TANPA /register: user baru cukup ketik namanya ----------

async def text_unregistered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group 1: text non-command utk chat yang BELUM terdaftar → perlakukan sbg nama.

    ConversationHandler ada di group 0 dan menangani update lebih dulu; flag WAITING
    memastikan kita tidak nyelak user yang lagi di tengah flow /register (ketik nama).
    """
    chat_id = update.effective_chat.id
    if users.get(chat_id):
        return  # sudah terdaftar — jangan ganggu
    name = update.message.text.strip()
    if not name:
        return
    if context.user_data.get(WAITING):
        return  # conversation /register aktif lagi menunggu nama — biarkan group 0
    if context.user_data.pop(REG_JUST, None) == name:
        return  # group 0 /register baru memproses nama ini — jangan dobel lookup
    await _lookup_and_save(
        name, chat_id, context,
        reply_text=update.message.reply_text,
        outside_conversation=True,
    )


async def pick_rgn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tombol pilih nama (banyak match) utk user tanpa sesi /register."""
    q = update.callback_query
    await q.answer()
    try:
        idx = int(q.data.split(":")[1])
    except (ValueError, IndexError):
        return
    matches = context.user_data.get(RGN_MATCHES, [])
    chat_id = update.effective_chat.id
    if not matches or idx < 0 or idx >= len(matches):
        await q.message.edit_text(
            "❌ Sesi pilihan nama sudah kedaluwarsa. Ketik namamu lagi ya."
        )
        return
    full_name = matches[idx]
    prev = await _save_name(chat_id, full_name, context)
    if prev and prev != full_name:
        await q.message.edit_text(f"✅ Nama diperbarui: {prev} → {full_name}")
    else:
        await q.message.edit_text(f"✅ Terdaftar sebagai: {full_name}")
    await q.message.reply_text("Kirim /zoom untuk mencatat kelas, /schedule untuk melihat jadwal.")
    context.user_data.pop(RGN_MATCHES, None)


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
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, reg_timeout)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=60 * 10,
        name="register_conv",
        allow_reentry=True,
    )
    app.add_handler(conv)
    REGISTER_CONV = conv
    _guard.register_conv("register_conv", conv)

    # Jalur "cukup ketik nama aja" utk user BARU (tanpa /register). Group 1: semua
    # polling update diproses tiap group (0 = ConversationHandler, 1 = ini), jadi
    # text_unregistered guard flag WAITING utk tidak nyelak flow /register di group 0.
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"^/"),
            text_unregistered,
        ),
        group=1,
    )
    app.add_handler(
        CallbackQueryHandler(pick_rgn, pattern=r"^rgn:\d+$"),
        group=1,
    )
