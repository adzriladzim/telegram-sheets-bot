"""/tukar — tukar jadwal fasil TANPA approval (kesepakatan via WA).

Siapa catat duluan menang (conflict ditolak di _append_swap, single-flight).
Pola: sekali (override tanggal itu saja) | tetap (pemilik berubah permanen)
| jam (tukar slot hari itu saja). Riwayat + undo /tukar_batal <id>, koreksi
admin /tukar_admin. Data di tab Tukar Jadwal (tab milik bot — master/backup/
makeup TIDAK disentuh). Read path baca swap dulu via sheets.get_classes merge.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime

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
from handlers.stats import _admin_ids, _admin_ok

log = logging.getLogger(__name__)

CLASS, KODE_DIA, PARTNER, POLA, TANGGAL, JAM, CONFIRM = range(7)
_TTIMEOUT = 60 * 60
_POLA_LABEL = {"sekali": "sekali", "tetap": "tetap", "jam": "jam"}


def _sheets(ctx): return ctx.bot_data["sheets"]


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


# ---------- entry ----------

async def cmd_tukar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
    name = users.get(update.effective_chat.id)
    if not name:
        await update.effective_message.reply_text(users.UNREGISTERED_MSG)
        return ConversationHandler.END
    busy = await st.loading(update, context, "⏳ Ambil daftar kelas...")
    try:
        classes = await _sheets(context).get_classes(name)
    except sheets.SheetsError as exc:
        await st.unbusy(busy)
        await update.effective_message.reply_text(f"⚠️ {exc}")
        return ConversationHandler.END
    await st.unbusy(busy)
    classes = [c for c in classes if c.category != "Tukar"]  # swap overlay jangan ditukar ulang
    if not classes:
        await update.effective_message.reply_text(
            "Tidak ada kelas dijadwalkan utk kamu — tukar butuh kelas milikmu di jadwal master.")
        return ConversationHandler.END
    context.user_data["tk_classes"] = classes
    await update.effective_message.reply_text(
        "🔁 <b>Tukar Jadwal</b> (tanpa approval — sepakati dulu via WA)\n\n"
        "1️⃣ Kelas kamu yang mau ditukar:",
        parse_mode=ParseMode.HTML, reply_markup=_class_kb(classes))
    return CLASS


def _class_kb(classes, prefix="tk") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        f"{c.code} — {html.escape(c.subject)} ({html.escape(c.day)} {html.escape(c.time_range)})",
        callback_data=f"{prefix}:{i}")] for i, c in enumerate(classes)]
    rows.append([InlineKeyboardButton("❌ Batal", callback_data=f"{prefix}:cancel")])
    return InlineKeyboardMarkup(rows)


# ---------- finish early ----------

async def _do_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q:
        await q.answer()
    await update.effective_message.reply_text("Dibatalkan.")
    return ConversationHandler.END


async def fallback_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return ConversationHandler.END


# ---------- step 1: pick my class ----------

async def pick_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    idx = int(q.data.split(":")[1])
    classes = context.user_data.get("tk_classes") or []
    if not 0 <= idx < len(classes):
        await q.message.reply_text("Pilihan kedaluwarsa — kirim /tukar lagi.")
        return ConversationHandler.END
    c = classes[idx]
    context.user_data["tk_cls"] = c
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="tk:back_class")]])
    await q.message.edit_text(
        f"Kelas kamu: <b>{html.escape(c.code)}</b> — {html.escape(c.subject)} "
        f"({html.escape(c.day)} {html.escape(c.time_range)})",
        parse_mode=ParseMode.HTML)
    await q.message.reply_text(
        "2️⃣ Kode kelas yang kamu TERIMA dari fasil lawan (kode kelas dia):",
        reply_markup=kb)
    return KODE_DIA


# ---------- step 2: partner's class code ----------

async def enter_kode_dia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    code = update.message.text.strip()
    if not code:
        return KODE_DIA
    try:
        ok = await _sheets(context).code_exists(code)
    except sheets.SheetsError as exc:
        await update.effective_message.reply_text(f"⚠️ {exc}")
        return KODE_DIA
    if not ok:
        await update.effective_message.reply_text(
            f"Kode <b>{html.escape(code)}</b> tidak ketemu di jadwal master. Cek ejaan (contoh: PPC01, InVC6).",
            parse_mode=ParseMode.HTML)
        return KODE_DIA
    mine = context.user_data["tk_cls"]
    if mine.code.casefold() == code.casefold():
        await update.effective_message.reply_text("Kode tidak boleh sama dengan kelas kamu.")
        return KODE_DIA
    context.user_data["tk_kode_dia"] = code.upper()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="tk:back_kode")]])
    await update.effective_message.reply_text(
        "3️⃣ Nama fasil lawan (yang megang kelas itu):", reply_markup=kb)
    return PARTNER


# ---------- step 3: partner name ----------

async def enter_partner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    search = update.message.text.strip()
    if not search:
        return PARTNER
    busy = await st.loading(update, context, "⏳ Cari nama fasil...")
    try:
        matches = await _sheets(context).find_facilitator_names(search)
    except sheets.SheetsError as exc:
        await st.unbusy(busy)
        await update.effective_message.reply_text(f"⚠️ {exc}")
        return PARTNER
    await st.unbusy(busy)
    mine = users.get(update.effective_chat.id) or ""
    matches = [m for m in matches if _norm_name(m) != _norm_name(mine)]
    if not matches:
        await update.effective_message.reply_text(
            f"Nama <b>{html.escape(search)}</b> tidak ketemu di jadwal master. Coba nama lengkap / alias.")
        return PARTNER
    if len(matches) == 1:
        return await _set_partner(update, context, matches[0])
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"👤 {html.escape(m)}", callback_data=f"tkp:{i}")]
         for i, m in enumerate(matches)] +
        [[InlineKeyboardButton("◀️ Kembali", callback_data="tk:back_partner")]])
    context.user_data["tk_partner_opts"] = matches
    await update.effective_message.reply_text("Banyak yang cocok — pilih salah satu:", reply_markup=kb)
    return PARTNER


async def _set_partner(update, context, name: str) -> int:
    context.user_data["tk_partner"] = name
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Sekali (tanggal itu saja)", callback_data="tkpola:sekali")],
        [InlineKeyboardButton("♾️ Tetap (ganti pemilik permanen)", callback_data="tkpola:tetap")],
        [InlineKeyboardButton("⌚ Jam (tukar slot hari itu saja)", callback_data="tkpola:jam")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="tk:back_partner")],
    ])
    msg = update.effective_message
    await msg.reply_text(
        f"4️⃣ Pola tukar (dengan <b>{html.escape(name)}</b>):", parse_mode=ParseMode.HTML, reply_markup=kb)
    return POLA


async def pick_partner_opt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    idx = int(q.data.split(":")[1])
    opts = context.user_data.get("tk_partner_opts") or []
    if not 0 <= idx < len(opts):
        await q.message.reply_text("Pilihan kedaluwarsa — kirim /tukar lagi.")
        return ConversationHandler.END
    return await _set_partner(update, context, opts[idx])


# ---------- step 4: pola ----------

async def pick_pola(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    pola = q.data.split(":")[1]
    if pola not in sheets.SWAP_POLA_OK:
        await q.message.reply_text("Pola tidak dikenal.")
        return POLA
    context.user_data["tk_pola"] = pola
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="tk:back_pola")]])
    if pola in ("sekali", "jam"):
        await q.message.edit_text(f"Pola: <b>{_POLA_LABEL[pola]}</b>")
        await q.message.reply_text(
            "5️⃣ Tanggal tukar (dd/mm/yyyy, contoh 25/9/2026):", reply_markup=kb)
        return TANGGAL
    await q.message.edit_text(f"Pola: <b>tetap</b> — tanpa tanggal.")
    return await _confirm_tukar(q.message, context)


# ---------- step 5: tanggal (sekali/jam) ----------

async def enter_tanggal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    t = sheets.SheetsClient._norm_tanggal(update.message.text)
    if not t:
        await update.effective_message.reply_text("Format tanggal: dd/mm/yyyy (contoh 25/9/2026).")
        return TANGGAL
    context.user_data["tk_tanggal"] = t
    pola = context.user_data.get("tk_pola")
    if pola == "jam":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="tk:back_tanggal")]])
        await update.effective_message.reply_text(
            "6️⃣ Jam/slot yang ditukar (opsional, contoh 13.00 - 15.30). Ketik '-' utk lewati:",
            reply_markup=kb)
        return JAM
    return await _confirm_tukar(update.effective_message, context)


# ---------- step 6: jam (pola jam) ----------

async def enter_jam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    jam = update.message.text.strip()
    jam = "" if jam in ("-", "") else jam
    context.user_data["tk_jam"] = jam
    return await _confirm_tukar(update.effective_message, context)


# ---------- confirm ----------

async def _confirm_tukar(msg, context) -> int:
    c = context.user_data.get("tk_cls")
    if c is None:
        await msg.reply_text("Sesi kadaluwarsa — kirim /tukar lagi.")
        return ConversationHandler.END
    pola = context.user_data.get("tk_pola")
    partner = context.user_data.get("tk_partner") or "-"
    kode_dia = context.user_data.get("tk_kode_dia") or "-"
    tanggal = context.user_data.get("tk_tanggal") or "-"
    jam = context.user_data.get("tk_jam") or ""
    lines = [
        "📋 <b>Konfirmasi Tukar:</b>",
        f"• Kelas kamu: {html.escape(c.code)} — {html.escape(c.subject)} ({html.escape(c.day)} {html.escape(c.time_range)})",
        f"• Terima dari: {html.escape(partner)} — kode {html.escape(kode_dia)}",
        f"• Pola: {pola}",
        f"• Tanggal: {html.escape(tanggal)}",
    ]
    if pola == "jam":
        lines.append(f"• Jam: {html.escape(jam or '-')}")
    lines.append("\nTanpa approval — sepakati via WA dulu. Submit?")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Catat", callback_data="tk:ok"), InlineKeyboardButton("❌ Batal", callback_data="tk:cancel")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="tk:back_pola")],
    ])
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb)
    return CONFIRM


async def confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    chat_id = update.effective_chat.id
    name = users.get(chat_id) or ""
    # Per-chat lock (konvensi sama dgn confirm_cb handler lain).
    async with _sheets(context).for_chat(chat_id):
        c = context.user_data.get("tk_cls")
        if c is None:
            await q.message.reply_text("Sesi kadaluwarsa — kirim /tukar lagi.")
            return ConversationHandler.END
        dari_wib = datetime.now(sheets.WIB).strftime("%Y-%m-%d %H:%M")
        rec = sheets.SwapRecord(
            no="",
            dicatat=dari_wib,
            oleh=name,
            kode_saya=c.code,
            tanggal=context.user_data.get("tk_tanggal") or "",
            dengan=context.user_data.get("tk_partner") or "",
            kode_dia=context.user_data.get("tk_kode_dia") or "",
            pola=context.user_data.get("tk_pola") or "",
            jam=context.user_data.get("tk_jam") or "",
        )
        busy = await st.saving(update, context, "⏳ Menyimpan tukar ke sheet...")
        try:
            no = await _sheets(context).append_swap(rec)
        except sheets.SheetsError as exc:
            await st.unbusy(busy)
            await q.message.reply_text(f"⚠️ Gagal: {exc}")
            return ConversationHandler.END
        await st.unbusy(busy)
        usage.log(chat_id, name, "tukar", rec.kode_saya, pola=rec.pola, tanggal=rec.tanggal)
        await q.message.reply_text(
            f"✅ <b>Tukar tercatat</b> id #{no}\n"
            f"{html.escape(rec.kode_saya)} ↔ {html.escape(rec.kode_dia)} ({rec.pola}"
            + (f", {html.escape(rec.tanggal)}" if rec.tanggal else "") + ")\n"
            f"Riwayat: /tukar_riwayat · Batalkan: /tukar_batal {no}",
            parse_mode=ParseMode.HTML)
        context.user_data.clear()
        return ConversationHandler.END


# ---------- /tukar_batal <id> ----------

async def cmd_batal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = (context.args or [])
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text("Pakai: /tukar_batal &lt;id&gt; (id dari /tukar_riwayat)")
        return
    row_id = args[0]
    r = update.effective_user
    uid, cid = (r.id if r else None), update.effective_chat.id
    name = users.get(update.effective_chat.id) or ""
    busy = await st.loading(update, context, "⏳ Membatalkan...")
    try:
        res, owner = await _sheets(context).cancel_swap_owned(
            row_id, name, _admin_ok(uid, cid, _admin_ids(context)), note="batalkan by chat")
    except sheets.SheetsError as exc:
        await st.unbusy(busy)
        await update.effective_message.reply_text(f"⚠️ {exc}")
        return
    await st.unbusy(busy)
    if res == 1:
        usage.log(update.effective_chat.id, name, "tukar_batal", row_id)
        await update.effective_message.reply_text(f"✅ Tukar #{row_id} dibatalkan. Jadwal balik ke semula.")
    elif res == -1:
        await update.effective_message.reply_text(f"Tukar #{row_id} sudah BATAL sebelumnya.")
    elif res == -2:
        await update.effective_message.reply_text(
            f"⛔ Hanya pencatat ({html.escape(owner or '-')}) atau admin yg bisa batal.")
    else:
        await update.effective_message.reply_text(f"Id #{row_id} tidak ketemu.")


# ---------- /tukar_riwayat ----------

async def cmd_riwayat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = (context.args or [])
    n = 15
    if args and args[0].isdigit():
        n = max(1, min(int(args[0]), 50))
    busy = await st.loading(update, context, "⏳ Ambil riwayat...")
    try:
        rows = await _sheets(context).list_swaps(limit=n)
    except sheets.SheetsError as exc:
        await st.unbusy(busy)
        await update.effective_message.reply_text(f"⚠️ {exc}")
        return
    await st.unbusy(busy)
    if not rows:
        await update.effective_message.reply_text("Belum ada riwayat tukar.")
        return
    lines = ["🔁 <b>Riwayat Tukar Jadwal</b>", ""]
    for sw in reversed(rows):
        mark = "✅" if sw.get("status") == sheets.SWAP_STATUS_AKTIF else "⛔"
        tgl = f" {html.escape(sw.get('tanggal') or 'tetap')}" if sw.get("tanggal") else " tetap"
        catatan = sw.get("catatan") or ""
        line = (f"{mark} <b>#{sw.get('no')}</b> {html.escape(sw.get('kode_saya') or '')} ↔ "
                f"{html.escape(sw.get('kode_dia') or '')} ({html.escape(sw.get('pola') or '')}{tgl})\n"
                f"       {html.escape(sw.get('oleh') or '-')} ⇄ {html.escape(sw.get('dengan') or '-')} · "
                f"{html.escape(sw.get('dicatat') or '')}")
        if catatan:
            line += f"\n       📝 {html.escape(catatan)}"
        lines.append(line)
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ---------- /tukar_admin ----------

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    r = update.effective_user
    uid, cid = (r.id if r else None), update.effective_chat.id
    if not _admin_ok(uid, cid, _admin_ids(context)):
        await update.effective_message.reply_text("⛔ Hanya admin.")
        return
    args = (context.args or [])
    if len(args) < 1 or not args[0].isdigit():
        await update.effective_message.reply_text(
            "Pakai:\n/tukar_admin &lt;id&gt; batal [alasan]\n/tukar_admin &lt;id&gt; hapus")
        return
    row_id = args[0]
    action = args[1].casefold() if len(args) > 1 else "batal"
    note = " ".join(args[2:])
    name = users.get(update.effective_chat.id) or "admin"
    if action == "hapus":
        res = await _sheets(context).cancel_swap(row_id, name, note=note, hard=True)
        if res == 1:
            usage.log(update.effective_chat.id, name, "tukar_admin_hapus", row_id, catatan=note)
            await update.effective_message.reply_text(f"🗑 Tukar #{row_id} dihapus permanen.")
        else:
            await update.effective_message.reply_text(f"Id #{row_id} tidak ketemu.")
        return
    res = await _sheets(context).cancel_swap(row_id, name, note=note or "koreksi admin")
    if res == 1:
        usage.log(update.effective_chat.id, name, "tukar_admin_batal", row_id, catatan=note)
        await update.effective_message.reply_text(f"✅ Tukar #{row_id} dibatalkan (admin).")
    elif res == -1:
        await update.effective_message.reply_text(f"Tukar #{row_id} sudah BATAL.")
    else:
        await update.effective_message.reply_text(f"Id #{row_id} tidak ketemu.")


# ---------- back navigation ----------

async def back_to_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    classes = context.user_data.get("tk_classes") or []
    if not classes:
        await q.message.reply_text("Sesi kadaluwarsa — kirim /tukar lagi.")
        return ConversationHandler.END
    await q.message.reply_text("1️⃣ Pilih kelas kamu:", reply_markup=_class_kb(classes))
    return CLASS


async def back_to_kode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    c = context.user_data.get("tk_cls")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="tk:back_class")]])
    await q.message.reply_text(
        f"2️⃣ Kode kelas yang kamu TERIMA (kode fasil lawan):\n\n"
        f"(kelas kamu: <b>{html.escape(c.code)}</b> — {html.escape(c.subject)})",
        parse_mode=ParseMode.HTML, reply_markup=kb)
    return KODE_DIA


async def back_to_partner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="tk:back_kode")]])
    await q.message.reply_text("3️⃣ Nama fasil lawan (yang megang kelas itu):", reply_markup=kb)
    return PARTNER


async def back_to_pola(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Sekali (tanggal itu saja)", callback_data="tkpola:sekali")],
        [InlineKeyboardButton("♾️ Tetap (ganti pemilik permanen)", callback_data="tkpola:tetap")],
        [InlineKeyboardButton("⌚ Jam (tukar slot hari itu saja)", callback_data="tkpola:jam")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="tk:back_partner")],
    ])
    partner = context.user_data.get("tk_partner") or "-"
    await q.message.reply_text(
        f"4️⃣ Pola tukar (dengan <b>{html.escape(partner)}</b>):",
        parse_mode=ParseMode.HTML, reply_markup=kb)
    return POLA


async def back_to_tanggal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="tk:back_pola")]])
    await q.message.reply_text("5️⃣ Tanggal tukar (dd/mm/yyyy, contoh 25/9/2026):", reply_markup=kb)
    return TANGGAL


def register(app: Application, cfg: Config) -> None:
    conv = ConversationHandler(
        entry_points=[CommandHandler("tukar", cmd_tukar),
                      CallbackQueryHandler(cmd_tukar, pattern=r"^go:tukar$")],
        states={
            CLASS: [CallbackQueryHandler(pick_class, pattern=r"^tk:\d+$"),
                    CallbackQueryHandler(_do_cancel, pattern=r"^tk:cancel$")],
            KODE_DIA: [CallbackQueryHandler(back_to_class, pattern=r"^tk:back_class$"),
                       MessageHandler(filters.TEXT & ~filters.COMMAND, enter_kode_dia)],
            PARTNER: [CallbackQueryHandler(back_to_kode, pattern=r"^tk:back_kode$"),
                      CallbackQueryHandler(pick_partner_opt, pattern=r"^tkp:\d+$"),
                      CallbackQueryHandler(back_to_partner, pattern=r"^tk:back_partner$"),
                      MessageHandler(filters.TEXT & ~filters.COMMAND, enter_partner)],
            POLA: [CallbackQueryHandler(pick_pola, pattern=r"^tkpola:(sekali|tetap|jam)$"),
                   CallbackQueryHandler(back_to_partner, pattern=r"^tk:back_partner$")],
            TANGGAL: [CallbackQueryHandler(back_to_pola, pattern=r"^tk:back_pola$"),
                      MessageHandler(filters.TEXT & ~filters.COMMAND, enter_tanggal)],
            JAM: [CallbackQueryHandler(back_to_tanggal, pattern=r"^tk:back_tanggal$"),
                  MessageHandler(filters.TEXT & ~filters.COMMAND, enter_jam)],
            CONFIRM: [CallbackQueryHandler(confirm_cb, pattern=r"^tk:ok$"),
                      CallbackQueryHandler(_do_cancel, pattern=r"^tk:cancel$"),
                      CallbackQueryHandler(back_to_pola, pattern=r"^tk:back_pola$")],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, fallback_cancel)],
        },
        fallbacks=[CommandHandler("tukar", cmd_tukar)],
        conversation_timeout=_TTIMEOUT, name="tukar_conv",
        allow_reentry=True,
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("tukar_batal", cmd_batal))
    app.add_handler(CommandHandler("tukar_riwayat", cmd_riwayat))
    app.add_handler(CommandHandler("tukar_admin", cmd_admin))