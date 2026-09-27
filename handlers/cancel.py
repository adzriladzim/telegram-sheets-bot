"""/cancel — lapor kelas cancel (B-I di Kelas Cancel & Pengganti)."""
from __future__ import annotations

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)

import sheets
import usage
import users
from config import Config
from handlers import status as st
from handlers import _guard

log = logging.getLogger(__name__)

CLASS, JADWAL, SESI, CONFIRM = range(4)
_TIMEOUT = 60 * 60

# Set di register() — referensi conv utk guard re-entry + cross-handler (S2).
CANCEL_CONV = None

def _sheets(ctx): return ctx.bot_data["sheets"]

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
    from telegram.constants import ChatAction
    # S2 guard: re-entry form yg sama -> jangan reset; handler lain aktif -> blok.
    g = await _guard.guard_entry(update, context, "cancel_conv", "cancel")
    if g is not None:
        return g
    # Prefill H+1 follow-up: h1c:<kode> cancel, h1r:<kode> cancel + catatan reschedule.
    pref = ""
    if update.callback_query and (qd := (update.callback_query.data or "")):
        if qd.startswith(("h1c:", "h1r:")):
            pref = qd.split(":", 1)[1].strip()
    if not update.callback_query or not (update.callback_query.data or "").startswith("h1r:"):
        context.user_data.pop("cc_note", None)  # bersihkan sisa reschedule lama
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    name = users.get(update.effective_chat.id)
    if not name:
        await update.effective_message.reply_text(users.UNREGISTERED_MSG)
        return ConversationHandler.END
    busy = await st.loading(update, context, "⏳ Ambil daftar kelas...")
    try:
        classes = await _sheets(context).get_classes(name)
    except sheets.SheetsError as e:
        await st.unbusy(busy)
        await update.effective_message.reply_text(f"⚠️ {e}")
        return ConversationHandler.END
    await st.unbusy(busy)
    if not classes:
        await update.effective_message.reply_text("Tidak ada kelas.")
        return ConversationHandler.END
    context.user_data["cc_classes"] = classes  # utk tombol ◀️ Kembali (jalur prefill)
    if pref:
        c = sheets.resolve_class_code(classes, pref)
        if c is not None:
            context.user_data["cc_cls"] = c
            if (update.callback_query.data or "").startswith("h1r:"):
                context.user_data["cc_note"] = "reschedule (dari follow-up H+1)"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="cc:back_class")]])
            await update.effective_message.reply_text(
                f"Kelas: <b>{html.escape(c.code)}</b> — {html.escape(c.subject)}\n\n"
                "2️⃣ Jadwal Awal? (contoh: Selasa, 9 September 2026)",
                parse_mode=ParseMode.HTML, reply_markup=kb)
            return JADWAL
        # kode tak ketemu di daftar kelas — fallback picker biasa (prefill best-effort)
    kb = _cc_class_kb(classes)
    await update.effective_message.reply_text("1️⃣ Pilih kelas yang cancel:", reply_markup=kb)
    return CLASS


def _cc_class_kb(classes) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"{c.code} — {c.subject} ({c.day} {c.time_range})", callback_data=f"cc:{i}")] for i, c in enumerate(classes)]
    rows.append([InlineKeyboardButton("❌ Batal", callback_data="cc:cancel")])
    return InlineKeyboardMarkup(rows)


async def back_to_cc_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    classes = context.user_data.get("cc_classes", [])
    if not classes:
        await q.message.reply_text("Kembali ke awal — kirim /cancel lagi.")
        return ConversationHandler.END
    await q.message.reply_text("1️⃣ Pilih kelas yang cancel:", reply_markup=_cc_class_kb(classes))
    return CLASS


async def back_to_cc_jadwal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="cc:back_class")]])
    await q.message.reply_text("2️⃣ Jadwal Awal? (contoh: Selasa, 9 September 2026)", reply_markup=kb)
    return JADWAL


async def back_to_cc_sesi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="cc:back_jadwal")]])
    await q.message.reply_text("3️⃣ Sesi/Pertemuan ke-? (angka 1-99)", reply_markup=kb)
    return SESI


async def back_cc_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Dibatalkan.")
    return ConversationHandler.END

async def pick_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    idx = int(q.data.split(":")[1])
    classes = context.user_data.get("cc_classes") or []
    if not 0 <= idx < len(classes):
        await q.message.reply_text("Pilihan kedaluwarsa — kirim /cancel lagi.")
        return ConversationHandler.END
    c = classes[idx]
    context.user_data["cc_cls"] = c
    await q.message.edit_text(f"Kelas: <b>{html.escape(c.code)}</b> — {html.escape(c.subject)}", parse_mode=ParseMode.HTML)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="cc:back_class")]])
    await q.message.reply_text("2️⃣ Jadwal Awal? (contoh: Selasa, 9 September 2026)", reply_markup=kb)
    return JADWAL

async def enter_jadwal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["cc_jadwal"] = update.message.text.strip()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="cc:back_jadwal")]])
    await update.effective_message.reply_text("3️⃣ Sesi/Pertemuan ke-? (angka 1-99)", reply_markup=kb)
    return SESI

async def enter_sesi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()
    if not t.isdigit() or not 1 <= int(t) <= 99:
        await update.effective_message.reply_text("Masukkan angka 1-99.")
        return SESI
    context.user_data["cc_sesi"] = t
    return await _confirm(update.message, context)

async def _confirm(msg, context):
    c = context.user_data["cc_cls"]
    chat_id = msg.chat_id if hasattr(msg, 'chat_id') else context.effective_chat.id
    name = users.get(chat_id) or ""
    rec = sheets.CancelRecord(c.lecturer, c.subject, context.user_data["cc_jadwal"], c.time_range, context.user_data["cc_sesi"], c.code, c.sks, name)
    txt = f"📋 <b>Konfirmasi Cancel:</b>\n• Dosen: {html.escape(rec.lecturer)}\n• Matkul: {html.escape(rec.subject)}\n• Jadwal Awal: {html.escape(rec.jadwal_awal)}\n• Jam: {html.escape(rec.jam)}\n• Sesi: {html.escape(rec.sesi)}\n• Kode: {html.escape(rec.kode)}\n• SKS: {html.escape(rec.sks)}\n• Fasil: {html.escape(rec.facilitator)}"
    # Catatan auto utk aksi "Reschedule" dari H+1 follow-up (display + user_data;
    # sheet J..R tetap READ-ONLY invariant — tidak ditulis ke sheet).
    cc_note = context.user_data.get("cc_note")
    if cc_note:
        txt += f"\n• Catatan: {html.escape(cc_note)}"
    txt += "\n\nSubmit?"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Submit", callback_data="cc:ok"), InlineKeyboardButton("❌ Batal", callback_data="cc:no")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="cc:back_sesi")],
    ])
    await msg.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
    return CONFIRM

async def confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    # Per-chat lock (see handlers/log.py confirm_cb).
    async with _sheets(context).for_chat(update.effective_chat.id):
        if q.data.endswith(":no"):
            await q.message.reply_text("Dibatalkan.")
            return ConversationHandler.END
        c = context.user_data["cc_cls"]
        chat_id = update.effective_chat.id
        name = users.get(chat_id) or ""
        rec = sheets.CancelRecord(c.lecturer, c.subject, context.user_data["cc_jadwal"], c.time_range, context.user_data["cc_sesi"], c.code, c.sks, name)
        busy = await st.saving(update, context, "⏳ Menyimpan ke sheet Cancel...")
        try:
            await _sheets(context).append_cancel_record(rec)
        except sheets.SheetsError as e:
            await st.unbusy(busy)
            await q.message.reply_text(f"⚠️ Gagal: {e}")
            return CONFIRM
        await st.unbusy(busy)
        usage.log(update.effective_chat.id, rec.facilitator, "cancel", rec.kode)
        await q.message.reply_text(f"✅ Cancel tercatat: {rec.kode} sesi {rec.sesi}")
        context.user_data.clear()
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text("Dibatalkan.")
    return ConversationHandler.END

def register(app: Application, cfg: Config) -> None:
    conv = ConversationHandler(
        entry_points=[CommandHandler("cancel", cmd_cancel),
                      CallbackQueryHandler(cmd_cancel, pattern=r"^go:cancel$"),
                      CallbackQueryHandler(cmd_cancel, pattern=r"^h1[cr]:")],
        states={
            CLASS: [CallbackQueryHandler(pick_class, pattern=r"^cc:\d+$"), CallbackQueryHandler(back_cc_cancel, pattern=r"^cc:cancel$")],
            JADWAL: [CallbackQueryHandler(back_to_cc_class, pattern=r"^cc:back_class$"), MessageHandler(filters.TEXT & ~filters.COMMAND, enter_jadwal)],
            SESI: [CallbackQueryHandler(back_to_cc_jadwal, pattern=r"^cc:back_jadwal$"), MessageHandler(filters.TEXT & ~filters.COMMAND, enter_sesi)],
            CONFIRM: [CallbackQueryHandler(confirm_cb, pattern=r"^cc:(ok|no)$"), CallbackQueryHandler(back_to_cc_sesi, pattern=r"^cc:back_sesi$")],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, cancel)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=_TIMEOUT, name="cancel_conv",
        allow_reentry=True,
    )
    app.add_handler(conv)
    CANCEL_CONV = conv
    _guard.register_conv("cancel_conv", conv)
