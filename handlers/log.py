"""/log — ConversationHandler: 6-step Zoom Record entry form."""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
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
import usage
import users
from config import Config

log = logging.getLogger(__name__)

CLASS, MEETING, SKEMA, ZOOM, NOTE, CONFIRM = range(6)
_TIMEOUT = 60 * 60  # 1 hour

_SKIP_FILTER = filters.TEXT & ~filters.COMMAND


def _sheets(context: ContextTypes.DEFAULT_TYPE) -> sheets.SheetsClient:
    return context.bot_data["sheets"]


async def _fetch_classes(context: ContextTypes.DEFAULT_TYPE, facilitator_name: str) -> list[sheets.ClassEntry]:
    classes = await _sheets(context).get_classes(facilitator_name)
    context.user_data["classes"] = classes
    return classes


# ---------- step 1: pick class ----------

async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from telegram.constants import ChatAction
    if update.callback_query:
        await update.callback_query.answer()
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    facilitator = users.get(update.effective_chat.id)
    if not facilitator:
        await update.effective_message.reply_text(users.UNREGISTERED_MSG)
        return ConversationHandler.END
    context.user_data["facilitator"] = facilitator
    loading = await update.effective_message.reply_text("⏳ Harap tunggu — ambil jadwal...")
    try:
        personal, backup = await _sheets(context).get_all_loggable_classes(facilitator)
    except sheets.SheetsError as exc:
        try: await loading.delete()
        except: pass
        await update.effective_message.reply_text(f"⚠️ {exc}")
        return ConversationHandler.END
    try: await loading.delete()
    except: pass
    classes = personal + backup
    if not classes:
        await update.effective_message.reply_text(f"Tidak ada kelas untuk {facilitator} (jadwal + backup kosong).")
        return ConversationHandler.END
    context.user_data["classes"] = classes
    # Check which classes have been filled for their last scheduled date + jam
    try:
        done_by_date = await _sheets(context).get_done_by_date(facilitator)
    except: done_by_date = set()
    kb_rows = []
    for i, c in enumerate(classes):
        last_date = sheets.last_date_for_day(c.day) if c.category != "Backup" else c.backup_hari_tanggal.split(",")[-1].strip() if "," in c.backup_hari_tanggal else c.backup_hari_tanggal
        if c.category == "Backup" and c.backup_hari_tanggal:
            try:
                last_date = _parse_backup_date(c.backup_hari_tanggal)
            except: pass
        is_done = (c.code.casefold(), last_date) in done_by_date
        prefix = "✅ " if is_done else ""
        if c.category == "Backup":
            label = f"{prefix}🔄 {c.code} — {c.subject} ({c.backup_hari_tanggal})"
        else:
            label = f"{prefix}{c.code} — {c.subject} ({c.day} {c.time_range})"
        kb_rows.append([InlineKeyboardButton(label, callback_data=f"c:{i}")])
    kb_rows.append([InlineKeyboardButton("❌ Batal", callback_data="back:cancel")])
    kb = InlineKeyboardMarkup(kb_rows)
    context.user_data["class_kb"] = kb_rows
    await update.effective_message.reply_text("1️⃣ Pilih kelas: (✅ = sudah isi untuk jadwal terakhir)", reply_markup=kb)
    return CLASS


def _meeting_kb(nxt: str, nxt2: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Pakai {nxt}", callback_data=f"m:{nxt}"),
         InlineKeyboardButton(f"🔄 Jadi {nxt2}", callback_data=f"m:{nxt2}")],
        [InlineKeyboardButton("✏️ Ketik manual", callback_data="m:manual")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="back:class")],
    ])


async def back_to_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    kb_rows = context.user_data.get("class_kb")
    if not kb_rows:
        await q.message.reply_text("Kembali ke awal — kirim /zoom lagi.")
        return ConversationHandler.END
    await q.message.reply_text("1️⃣ Pilih kelas:", reply_markup=InlineKeyboardMarkup(kb_rows))
    return CLASS


async def back_to_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    c = context.user_data.get("cls")
    last, nxt, nxt2 = context.user_data.get("suggest", ("", "1", "1 dan 2"))
    kb = _meeting_kb(nxt, nxt2)
    if last and c:
        await q.message.reply_text(f"2️⃣ Terakhir {c.code} pertemuan {last}. Mau isi {nxt}?", reply_markup=kb)
    else:
        await q.message.reply_text(f"2️⃣ Pertemuan ke-berapa? (suggest {nxt})", reply_markup=kb)
    return MEETING


async def back_to_scheme(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Online", callback_data="s:o"),
         InlineKeyboardButton("🏫 Offline", callback_data="s:f")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="back:meeting")],
    ])
    await q.message.reply_text("3️⃣ Skema kelas:", reply_markup=kb)
    return SKEMA


async def back_to_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip (tidak ada catatan)", callback_data="n:skip")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="back:scheme")],
    ])
    await q.message.reply_text("5️⃣ Catatan fasil? Ketik teks atau tekan Skip:", reply_markup=kb)
    return NOTE


async def back_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Dibatalkan.")
    return ConversationHandler.END


async def pick_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split(":")[1])
    classes: list[sheets.ClassEntry] = context.user_data["classes"]
    c = classes[idx]
    context.user_data["cls"] = c
    await q.message.edit_text(f"Kelas: <b>{c.code}</b> — {c.subject}\n\n", parse_mode=ParseMode.HTML)
    # Auto-suggest next meeting
    wait = await q.message.reply_text("⏳ Cari pertemuan terakhir...")
    try:
        last, nxt, nxt2 = await _sheets(context).get_next_meeting(c.code)
    except Exception:
        last, nxt, nxt2 = "", "1", "1 dan 2"
    try: await wait.delete()
    except: pass
    context.user_data["suggest"] = (last, nxt, nxt2)
    context.user_data["meeting"] = nxt  # auto-sync dari Zoom Record, tanpa tanya
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Online", callback_data="s:o"),
         InlineKeyboardButton("🏫 Offline", callback_data="s:f")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="back:meeting")],
    ])
    auto_note = f"(auto pertemuan {nxt}" + (f", terakhir {last}" if last else "") + ")"
    await q.message.reply_text(f"2️⃣ Pertemuan: <b>{nxt}</b> {auto_note}\n3️⃣ Skema kelas:", parse_mode=ParseMode.HTML, reply_markup=kb)
    return SKEMA


# ---------- step 2: meeting number ----------

async def pick_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    val = q.data.split(":", 1)[1]
    if val == "manual":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="back:class")]])
        await q.message.reply_text("Ketik pertemuan (contoh: 3 atau '3 dan 4'):", reply_markup=kb)
        return MEETING
    context.user_data["meeting"] = val
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Online", callback_data="s:o"), InlineKeyboardButton("🏫 Offline", callback_data="s:f")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="back:meeting")],
    ])
    await q.message.reply_text("3️⃣ Skema kelas:", reply_markup=kb)
    return SKEMA

async def enter_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    import re
    text = update.message.text.strip()
    nums = re.findall(r"\d+", text)
    if not nums or not all(1 <= int(n) <= 99 for n in nums):
        await update.effective_message.reply_text("Masukkan pertemuan 1–99 (bisa 1, 2 atau '1 dan 2').")
        return MEETING
    context.user_data["meeting"] = text
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🌐 Online", callback_data="s:o"),
          InlineKeyboardButton("🏫 Offline", callback_data="s:f")],
         [InlineKeyboardButton("◀️ Kembali", callback_data="back:meeting")]]
    )
    await update.effective_message.reply_text("3️⃣ Skema kelas:", reply_markup=kb)
    return SKEMA


# ---------- step 3: scheme ----------

async def pick_scheme(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    online = q.data.endswith(":o")
    context.user_data["scheme"] = "Online" if online else "Offline"
    # Zoom auto dari Jadwal Fasil (Zoom XX), ga perlu input
    c: sheets.ClassEntry = context.user_data["cls"]
    context.user_data["zoom"] = c.zoom_label
    zoom_info = f"Zoom: {c.zoom_label}" if c.zoom_label else "Zoom: —"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip (tidak ada catatan)", callback_data="n:skip")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="back:scheme")],
    ])
    await q.message.reply_text(f"4️⃣ {zoom_info} (auto dari Jadwal)\n5️⃣ Catatan fasil? Ketik teks atau tekan Skip.", reply_markup=kb)
    return NOTE


# ---------- step 4: zoom ----------

async def enter_zoom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    # Normalize: "33" -> "Zoom 33", "zoom 33" -> "Zoom 33", "Zoom 33" -> "Zoom 33", link stays as is
    if raw.isdigit():
        raw = f"Zoom {raw}"
    elif raw.lower().startswith("zoom "):
        num = raw[5:].strip()
        if num.isdigit():
            raw = f"Zoom {num}"
    context.user_data["zoom"] = raw
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("⏭ Skip (tidak ada catatan)", callback_data="n:skip")]]
    )
    await update.effective_message.reply_text("5️⃣ Catatan fasil? Ketik teks, atau tekan tombol:", reply_markup=kb)
    return NOTE


# ---------- step 5: notes ----------

async def enter_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["notes"] = update.message.text.strip()
    return await _confirm(update.message, context)


async def skip_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["notes"] = ""
    return await _confirm(update.message, context)


async def skip_note_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    context.user_data["notes"] = ""
    return await _confirm(q.message, context)


# ---------- step 6: confirm ----------

def _parse_backup_date(s: str) -> str:
    """'Senin, 8 September 2026' -> '08/09/2026'."""
    import re
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", s)
    if not m:
        return sheets.today_str_wib()
    d, mon, y = m.group(1), m.group(2).lower(), m.group(3)
    months = {"januari": "01", "februari": "02", "maret": "03", "april": "04", "mei": "05", "juni": "06", "juli": "07", "agustus": "08", "september": "09", "oktober": "10", "november": "11", "desember": "12"}
    mm = months.get(mon, "01")
    return f"{int(d):02d}/{mm}/{y}"

def _build_record(context: ContextTypes.DEFAULT_TYPE) -> sheets.LogRecord:
    c: sheets.ClassEntry = context.user_data["cls"]
    # Zoom: user input (step 4) OR auto from Jadwal Fasil
    zoom = context.user_data.get("zoom", "") or c.zoom_label
    # Lecture date: backup uses its Hari/Tanggal, personal uses next occurrence of day
    if c.category == "Backup" and c.backup_hari_tanggal:
        lecture_date = _parse_backup_date(c.backup_hari_tanggal)
    else:
        lecture_date = sheets.next_date_for_day(c.day)
    return sheets.LogRecord(
        facilitator=context.user_data.get("facilitator") or context.bot_data["cfg"].facilitator_name,
        lecture_date=lecture_date,
        entry_date=sheets.today_str_wib(),
        semester=c.semester or context.bot_data["cfg"].semester,
        subject=c.subject,
        code=c.code,
        meeting=context.user_data.get("meeting", ""),
        scheme=context.user_data.get("scheme", ""),
        sks=c.sks,
        tipe_kelas=c.tipe_kelas,
        lecturer=c.lecturer,
        start_time=c.start_time,
        zoom=zoom,
        notes=context.user_data.get("notes", ""),
    )


def _summary(rec: sheets.LogRecord) -> str:
    rows = [
        ("Tanggal pengisian", rec.entry_date), ("Fasilitator", rec.facilitator),
        ("Tanggal kelas", rec.lecture_date), ("Semester", rec.semester),
        ("Mata kuliah", rec.subject), ("Kelas", rec.code),
        ("Pertemuan", rec.meeting), ("Skema", rec.scheme),
        ("SKS", rec.sks), ("Tipe", rec.tipe_kelas),
        ("Dosen", rec.lecturer), ("Jam mulai", rec.start_time),
        ("Zoom", rec.zoom or "—"), ("Catatan", rec.notes or "—"),
    ]
    lines = "\n".join(f"• <b>{k}:</b> {v}" for k, v in rows)
    return f"📋 <b>Konfirmasi data:</b>\n{lines}\n\nLanjut submit?"


async def _confirm(message: Message, context: ContextTypes.DEFAULT_TYPE) -> int:
    rec = _build_record(context)
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Submit", callback_data="x:ok"),
          InlineKeyboardButton("❌ Batal", callback_data="x:no")],
         [InlineKeyboardButton("◀️ Kembali", callback_data="back:note")]]
    )
    await message.reply_text(_summary(rec), parse_mode=ParseMode.HTML, reply_markup=kb)
    return CONFIRM


async def confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    # Per-chat lock: same-chat updates can run concurrently now (concurrent_updates=True).
    # Serialize the gspread write so double-tap Submit can't double-write.
    async with _sheets(context).for_chat(update.effective_chat.id):
        if q.data.endswith(":no"):
            await q.message.reply_text("Dibatalkan. Kirim /log untuk coba lagi.")
            return ConversationHandler.END
        rec = _build_record(context)
        retries = context.user_data.get("_retries", 0)
        busy = await q.message.reply_text("⏳ Menyimpan ke Zoom Record...")
        try:
            await _sheets(context).append_record(rec)
        except sheets.SheetsError as exc:
            retries += 1
            context.user_data["_retries"] = retries
            try: await busy.delete()
            except: pass
            if retries >= 3:
                await q.message.reply_text(f"⚠️ Gagal 3x: {exc}\nData tidak tersimpan. Kirim /log untuk mulai ulang.")
                context.user_data.clear()
                return ConversationHandler.END
            await q.message.reply_text(f"⚠️ Gagal simpan ({retries}/3): {exc}\nTekan ✅ untuk retry, atau /cancel untuk batal.")
            return CONFIRM
        try: await busy.delete()
        except: pass
        log.info("Saved record: %s mtg %s scheme %s", rec.code, rec.meeting, rec.scheme)
        usage.log(update.effective_chat.id, rec.facilitator, "zoom", rec.code)
        await q.message.reply_text(f"✅ Tercatat di sheet! {rec.code} — pertemuan {rec.meeting} ({rec.scheme}).\nKirim /log untuk entry berikutnya.")
        context.user_data.clear()
        return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text("Form dibatalkan.")
    return ConversationHandler.END


async def timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text("⏱ Form timeout (1 jam). Kirim /log untuk mulai lagi.")
    return ConversationHandler.END


def register(app: Application, cfg: Config) -> None:
    conv = ConversationHandler(
        entry_points=[CommandHandler(["log", "zoom", "zoom_record"], cmd_log),
                      CallbackQueryHandler(cmd_log, pattern=r"^go:log$")],
        states={
            CLASS: [CallbackQueryHandler(pick_class, pattern=r"^c:\d+$"), CallbackQueryHandler(back_cancel, pattern=r"^back:cancel$")],
            MEETING: [CallbackQueryHandler(pick_meeting, pattern=r"^m:"), CallbackQueryHandler(back_to_class, pattern=r"^back:class$"), MessageHandler(_SKIP_FILTER, enter_meeting)],
            SKEMA: [CallbackQueryHandler(pick_scheme, pattern=r"^s:[of]$"), CallbackQueryHandler(back_to_meeting, pattern=r"^back:meeting$")],
            ZOOM: [MessageHandler(_SKIP_FILTER, enter_zoom)],
            NOTE: [
                CallbackQueryHandler(skip_note_cb, pattern=r"^n:skip$"),
                CallbackQueryHandler(back_to_scheme, pattern=r"^back:scheme$"),
                CommandHandler("skip", skip_note),
                MessageHandler(_SKIP_FILTER, enter_note),
            ],
            CONFIRM: [CallbackQueryHandler(confirm_cb, pattern=r"^x:(ok|no)$"), CallbackQueryHandler(back_to_note, pattern=r"^back:note$")],
            # v22: on_timeout param removed — next update after timeout lands in TIMEOUT state
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, timeout)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", cancel), CommandHandler(["log", "zoom", "zoom_record"], cmd_log)],
        conversation_timeout=_TIMEOUT,
        name="log_conv",
    )
    app.add_handler(conv)
