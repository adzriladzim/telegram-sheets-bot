"""/backup — izin & backup fasil."""
from __future__ import annotations

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)

import sheets
import usage
import users
from config import Config
from handlers import status as st

log = logging.getLogger(__name__)

CLASS, TANGGAL, PENGGANTI, CATATAN, CONFIRM = range(5)
_TIMEOUT = 60 * 60

def _sheets(ctx): return ctx.bot_data["sheets"]

async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    name = users.get(update.effective_chat.id)
    if not name:
        await update.effective_message.reply_text(users.UNREGISTERED_MSG)
        return ConversationHandler.END
    loading = await update.effective_message.reply_text("⏳ Harap tunggu — ambil jadwal...")
    try:
        classes = await _sheets(context).get_classes(name)
    except sheets.SheetsError as e:
        try: await loading.delete()
        except Exception: pass
        await update.effective_message.reply_text(f"⚠️ {e}")
        return ConversationHandler.END
    try: await loading.delete()
    except Exception: pass
    if not classes:
        await update.effective_message.reply_text("Tidak ada kelas.")
        return ConversationHandler.END
    kb = _bk_class_kb(classes)
    context.user_data["bk_classes"] = classes
    await update.effective_message.reply_text("1️⃣ Pilih kelas yang mau dibackup:", reply_markup=kb)
    return CLASS


def _bk_class_kb(classes) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"{c.code} — {c.subject} ({c.day} {c.time_range})", callback_data=f"bk:{i}")] for i, c in enumerate(classes)]
    rows.append([InlineKeyboardButton("❌ Batal", callback_data="bk:cancel")])
    return InlineKeyboardMarkup(rows)


async def back_to_bk_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    classes = context.user_data.get("bk_classes", [])
    if not classes:
        await q.message.reply_text("Kembali ke awal — kirim /backup lagi.")
        return ConversationHandler.END
    await q.message.reply_text("1️⃣ Pilih kelas yang mau dibackup:", reply_markup=_bk_class_kb(classes))
    return CLASS


async def back_to_bk_tanggal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="bk:back_class")]])
    await q.message.reply_text("2️⃣ Hari/Tanggal izin? (contoh: Senin, 8 September 2026)", reply_markup=kb)
    return TANGGAL


async def back_to_bk_pengganti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="bk:back_tanggal")]])
    await q.message.reply_text("3️⃣ Fasil pengganti? (nama)", reply_markup=kb)
    return PENGGANTI


async def back_to_bk_catatan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip", callback_data="bk:skip_note")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="bk:back_pengganti")],
    ])
    await q.message.reply_text("4️⃣ Catatan? Ketik atau Skip:", reply_markup=kb)
    return CATATAN


async def back_bk_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Dibatalkan.")
    return ConversationHandler.END

async def pick_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    idx = int(q.data.split(":")[1])
    classes = context.user_data.get("bk_classes") or []
    if not 0 <= idx < len(classes):
        await q.message.reply_text("Pilihan kedaluwarsa — kirim /backup lagi.")
        return ConversationHandler.END
    c = classes[idx]
    context.user_data["bk_cls"] = c
    await q.message.edit_text(f"Kelas: <b>{html.escape(c.code)}</b> — {html.escape(c.subject)}", parse_mode=ParseMode.HTML)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="bk:back_class")]])
    await q.message.reply_text("2️⃣ Hari/Tanggal izin? (contoh: Senin, 8 September 2026)", reply_markup=kb)
    return TANGGAL

async def enter_tanggal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["bk_tanggal"] = update.message.text.strip()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="bk:back_tanggal")]])
    await update.effective_message.reply_text("3️⃣ Fasil pengganti? (nama)", reply_markup=kb)
    return PENGGANTI

async def enter_pengganti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    try:
        matches = await _sheets(context).find_facilitator_names(raw)
    except sheets.SheetsError:
        matches = []
    if len(matches) == 1:
        full = matches[0]
        context.user_data["bk_pengganti"] = full
        await update.effective_message.reply_text(f"Pengganti: {html.escape(raw)} → <b>{html.escape(full)}</b>", parse_mode=ParseMode.HTML)
    elif len(matches) > 1:
        context.user_data["bk_pengganti_raw"] = raw
        context.user_data["bk_pengganti_matches"] = matches
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(m, callback_data=f"bkp:{i}")] for i, m in enumerate(matches)])
        await update.effective_message.reply_text(f"🔍 {len(matches)} nama cocok untuk \"{raw}\", pilih:", reply_markup=kb)
        return PENGGANTI
    else:
        context.user_data["bk_pengganti"] = raw
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip", callback_data="bk:skip_note")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="bk:back_pengganti")],
    ])
    await update.effective_message.reply_text("4️⃣ Catatan? Ketik atau Skip:", reply_markup=kb)
    return CATATAN

async def pick_pengganti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    idx = int(q.data.split(":")[1])
    matches = context.user_data.get("bk_pengganti_matches", [])
    full = matches[idx] if idx < len(matches) else ""
    context.user_data["bk_pengganti"] = full
    await q.message.edit_text(f"Pengganti: <b>{html.escape(full)}</b>", parse_mode=ParseMode.HTML)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip", callback_data="bk:skip_note")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="bk:back_pengganti")],
    ])
    await q.message.reply_text("4️⃣ Catatan? Ketik atau Skip:", reply_markup=kb)
    return CATATAN

async def enter_catatan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["bk_catatan"] = update.message.text.strip()
    return await _confirm(update.message, context)

async def skip_catatan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    context.user_data["bk_catatan"] = ""
    return await _confirm(q.message, context)

async def skip_catatan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["bk_catatan"] = ""
    return await _confirm(update.message, context)

def _summary(r):
    return (f"📋 <b>Konfirmasi Backup:</b>\n"
            f"• Fasil Awal: {html.escape(r.facilitator_awal)}\n"
            f"• Hari/Tgl: {html.escape(r.hari_tanggal)}\n"
            f"• Jam: {html.escape(r.jam)}\n"
            f"• Kelas: {html.escape(r.kode)} — {html.escape(r.subject)}\n"
            f"• Dosen: {html.escape(r.lecturer)}\n"
            f"• Ruang: {html.escape(r.room)}\n"
            f"• Pengganti: {html.escape(r.pengganti)}\n"
            f"• Catatan: {html.escape(r.catatan or '—')}\n\nSubmit?")

async def _confirm(msg, context):
    chat_id = msg.chat_id if hasattr(msg, 'chat_id') else context.effective_chat.id
    name = users.get(chat_id) or ""
    c = context.user_data["bk_cls"]
    rec = sheets.BackupRecord(name, context.user_data["bk_tanggal"], c.time_range, c.code, c.subject, c.lecturer, c.room, context.user_data["bk_pengganti"], context.user_data.get("bk_catatan",""))
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Submit", callback_data="bk:ok"), InlineKeyboardButton("❌ Batal", callback_data="bk:no")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="bk:back_catatan")],
    ])
    await msg.reply_text(_summary(rec), parse_mode=ParseMode.HTML, reply_markup=kb)
    return CONFIRM

async def confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    # Per-chat lock (see handlers/log.py confirm_cb).
    async with _sheets(context).for_chat(update.effective_chat.id):
        if q.data.endswith(":no"):
            await q.message.reply_text("Dibatalkan.")
            return ConversationHandler.END
        chat_id = update.effective_chat.id
        name = users.get(chat_id) or ""
        c = context.user_data["bk_cls"]
        rec = sheets.BackupRecord(name, context.user_data["bk_tanggal"], c.time_range, c.code, c.subject, c.lecturer, c.room, context.user_data["bk_pengganti"], context.user_data.get("bk_catatan",""))
        busy = await st.saving(update, context, "⏳ Menyimpan ke sheet Backup...")
        try:
            await _sheets(context).append_backup_record(rec)
        except sheets.SheetsError as e:
            await st.unbusy(busy)
            await q.message.reply_text(f"⚠️ Gagal: {e}\nCoba lagi.")
            return CONFIRM
        await st.unbusy(busy)
        usage.log(update.effective_chat.id, rec.facilitator_awal, "backup", rec.kode)
        await q.message.reply_text(f"✅ Backup tercatat: {rec.kode} → {rec.pengganti}")
        context.user_data.clear()
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text("Dibatalkan.")
    return ConversationHandler.END

def register(app: Application, cfg: Config) -> None:
    conv = ConversationHandler(
        entry_points=[CommandHandler("backup", cmd_backup),
                      CallbackQueryHandler(cmd_backup, pattern=r"^go:backup$")],
        states={
            CLASS: [CallbackQueryHandler(pick_class, pattern=r"^bk:\d+$"), CallbackQueryHandler(back_bk_cancel, pattern=r"^bk:cancel$")],
            TANGGAL: [CallbackQueryHandler(back_to_bk_class, pattern=r"^bk:back_class$"), MessageHandler(filters.TEXT & ~filters.COMMAND, enter_tanggal)],
            PENGGANTI: [CallbackQueryHandler(pick_pengganti, pattern=r"^bkp:\d+$"), CallbackQueryHandler(back_to_bk_tanggal, pattern=r"^bk:back_tanggal$"), MessageHandler(filters.TEXT & ~filters.COMMAND, enter_pengganti)],
            CATATAN: [CallbackQueryHandler(skip_catatan, pattern=r"^bk:skip_note$"), CallbackQueryHandler(back_to_bk_pengganti, pattern=r"^bk:back_pengganti$"), CommandHandler("skip", skip_catatan_cmd), MessageHandler(filters.TEXT & ~filters.COMMAND, enter_catatan)],
            CONFIRM: [CallbackQueryHandler(confirm_cb, pattern=r"^bk:(ok|no)$"), CallbackQueryHandler(back_to_bk_catatan, pattern=r"^bk:back_catatan$")],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, cancel)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=_TIMEOUT, name="backup_conv",
        allow_reentry=True,
    )
    app.add_handler(conv)
