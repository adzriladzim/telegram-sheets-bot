"""/reminder_dosen — template chat WhatsApp/Telegram ke dosen.

Flow (user-approved FINAL):
1. Picker kelas grup bertingkat (reuse handlers.log._group_picker_classes):
   📅 minggu ini / ⏳ tunggakan / ✅ sudah di-log (default tersembunyi).
   Kelas milik user yg sedang didelegasikan ke backup ikut tampil (🔀) —
   get_classes(include_delegated=True).
2. Pilih jenis template: 1 Normal (pengingat) / 2 Backup (digantikan) /
   3 Reschedule (ubah jadwal).
3. Tanggal: backup/make-up tanggal eksplisit; personal step DATE picker kalau
   last != next (default jadwal berikutnya — jangan last include today).
4. Data tambahan best-effort:
   - Backup: pengganti dari baris Backup (kode+tanggal) kalau ada; kalau tidak
     tanya 'siapa penggantinya?' (text).
   - Reschedule: jadwal baru prefill dari Cancel (jadwal make-up) / Tukar
     (swap_tanggal); kalau tidak tanya (text).
5. 'Bu atau Pak utk sapaan dosen?' -> [$ Bu] [$ Pak] -> template final.
   Sapaan WIB: 04-10 pagi / 11-14 siang / 15-18 sore / 19-03 malam.
6. Pesan final = [info line 📖 Kelas: judul — kode (display only, BUKAN
   payload)] + [template] + tombol [📋 Salin] (payload = template only,
   CopyTextButton PTB v22.8 — plain text, TANPA html escape; display saja
   yang html.escape). GUARD: payload > 256 (kode aneh/panjang) -> fallback
   kirim tanpa tombol salin + hint "Tahan pesan utk salin" (tidak crash).

Read-only flow (tidak tulis sheet) — semua baca via _run wrapper.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime

from telegram import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ChatAction, ParseMode
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
from handlers import _guard, log as hlog

log = logging.getLogger(__name__)

CLASS, TYPE, DATE, PENGGANTI, JADWAL_BARU, SAPA = range(6)
_TIMEOUT = 60 * 60  # 1 hour

_SKIP_FILTER = filters.TEXT & ~filters.COMMAND

# Set di register() — referensi conv utk guard re-entry + cross-handler (S2).
REMINDER_DOSEN_CONV = None

# Tipe template (indeks tombol t:1..3).
T_NORMAL, T_BACKUP, T_RESCHEDULE = 1, 2, 3


def _sheets(context: ContextTypes.DEFAULT_TYPE) -> sheets.SheetsClient:
    return context.bot_data["sheets"]


# ---------- sapaan WIB ----------

def _sapaan_wib(now: datetime | None = None) -> str:
    """Kata sifat sapaan WIB ('pagi'/'siang'/'sore'/'malam') — template sudah
    memuat 'Selamat <sapaan>', jadi slot TANPA 'Selamat' (hindari dobel).
    `now` (WIB-aware) hanya untuk test."""
    h = (now or datetime.now(sheets.WIB)).hour
    if 4 <= h <= 10:
        return "pagi"
    if 11 <= h <= 14:
        return "siang"
    if 15 <= h <= 18:
        return "sore"
    return "malam"


# ---------- tanggal kelas ----------

def _date_for(c: sheets.ClassEntry, qdata: str | None = None) -> str:
    """dd/mm/yyyy tanggal kelas utk template. Backup/make-up eksplisit;
    personal default jadwal BERIKUTNYA (bukan last include today); d:last = pilihan user."""
    if c.category in ("Backup", "Make-up") and c.backup_hari_tanggal:
        return sheets.parse_backup_date(c.backup_hari_tanggal)
    if qdata == "d:last":
        return sheets.last_date_for_day(c.day)
    return sheets.next_date_for_day(c.day)


def _needs_date_step(c: sheets.ClassEntry) -> bool:
    """Personal ambigu (last != next) -> step DATE picker; eksplisit -> langsung."""
    if c.category in ("Backup", "Make-up") and c.backup_hari_tanggal:
        return False
    return sheets.last_date_for_day(c.day) != sheets.next_date_for_day(c.day)


def _tanggal_line(c: sheets.ClassEntry, tgl: str) -> tuple[str, str, str]:
    """(Hari, Tanggal panjang, Jam) utk baris 📅/⏰ template."""
    d = hlog._dmy_to_date(tgl)
    return sheets.DAY_ORDER[d.weekday()], sheets.tanggal_panjang(tgl), c.time_range


# ---------- prefill backup / reschedule ----------

def _jadwal_sebelumnya(c: sheets.ClassEntry, tgl: str, csched: dict | None) -> str:
    """🔙 Jadwal sebelumnya: make-up pakai jadwal awal baris Cancel (F), else
    derived dari day + anchor tanggal + jam."""
    if c.category == "Make-up" and csched and csched.get("jadwal_awal"):
        jam = csched.get("jam") or c.time_range
        return f"{csched['jadwal_awal']}, {jam} WIB"
    hari, tgl_panjang, jam = _tanggal_line(c, tgl)
    return f"{hari}, {tgl_panjang}, {jam} WIB"


def _prefill_jadwal_baru(c: sheets.ClassEntry, csched: dict | None) -> str:
    """✅ Jadwal baru: prefill dari Cancel (L+M) / Tukar (swap_tanggal); '' = minta user."""
    if csched and csched.get("jadwal_makeup"):
        jam = csched.get("jam_makeup") or ""
        base = f"{csched['jadwal_makeup']}"
        return f"{base}, {jam} WIB" if jam else f"{base} WIB"
    if c.swap_tanggal:
        return f"{sheets.tanggal_panjang(c.swap_tanggal)}, {c.time_range} WIB"
    return ""


# ---------- renderer template (EXACT wording — jangan ubah) ----------

def render_normal(c: sheets.ClassEntry, sapa: str, bu_pak: str, tgl: str) -> str:
    hari, tgl_panjang, jam = _tanggal_line(c, tgl)
    return (
        f"Selamat {sapa} {bu_pak} {c.lecturer} 🙏\n\n"
        f"Izin mengingatkan untuk kelas yang akan berlangsung ya, {bu_pak}:\n\n"
        f"📚 {c.code}\n"
        f"📅 {hari}, {tgl_panjang}\n"
        f"⏰ {jam} WIB\n\n"
        f"Kalau ada yang ingin ditanyakan seputar kelas nanti, boleh banget "
        f"hubungi saya. Terima kasih, {bu_pak} 🙏"
    )


def render_backup(c: sheets.ClassEntry, sapa: str, bu_pak: str, tgl: str,
                  pengganti: str, fasil_awal: str) -> str:
    hari, tgl_panjang, jam = _tanggal_line(c, tgl)
    return (
        f"Selamat {sapa} {bu_pak} {c.lecturer} 🙏\n\n"
        f"Izin info, kelas nanti digantikan fasilitator lain ya, {bu_pak}:\n\n"
        f"📚 {c.code}\n"
        f"📅 {hari}, {tgl_panjang}\n"
        f"⏰ {jam} WIB\n"
        f"👤 Pengganti: {pengganti} (menggantikan {fasil_awal})\n\n"
        f"Kelas tetap jalan seperti jadwal ya, {bu_pak}. Terima kasih 🙏"
    )


def render_reschedule(c: sheets.ClassEntry, sapa: str, bu_pak: str, tgl: str,
                      sebelumnya: str, jadwal_baru: str) -> str:
    return (
        f"Selamat {sapa} {bu_pak} {c.lecturer} 🙏\n\n"
        f"Izin info, jadwal kelas berubah, {bu_pak}:\n\n"
        f"📚 {c.code}\n"
        f"🔙 Jadwal lama: {sebelumnya}\n"
        f"✅ Jadwal baru: {jadwal_baru}\n\n"
        f"Mohon maaf, terima kasih atas pengertiannya ya, {bu_pak} 🙏"
    )


def _display_copy(plain: str) -> tuple[str, str]:
    """(display html.escape, payload tombol Salin plain)."""
    return html.escape(plain), plain


# Guard CopyTextButton: payload limit 256 — kode aneh/panjang -> fallback
# tanpa tombol salin (long-press utk salin), jangan crash.
_TG_COPY_LIMIT = 256


def _final_message(plain: str, subject: str, code: str) -> tuple[str, InlineKeyboardMarkup]:
    """(final display HTML, kb) — info line '📖 Kelas: judul — kode' hanya
    di display; payload tombol Salin = template only. GUARD len(plain)>256
    -> kirim tanpa tombol Salin + hint 'Tahan pesan utk salin'."""
    info = f"📖 Kelas: {html.escape(subject)} — {html.escape(code)}"
    if len(plain) <= _TG_COPY_LIMIT:
        disp, payload = _display_copy(plain)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Salin", copy_text=CopyTextButton(payload))],
            [InlineKeyboardButton("🔄 Template lain", callback_data="t:again")],
        ])
        return (
            "✅ Template siap — tekan <b>📋 Salin</b> lalu tempel ke WhatsApp/Telegram dosen:\n\n"
            f"{info}\n\n"
            f"{disp}\n\n"
            "<i>Ketik /reminder_dosen utk template lain.</i>"
        ), kb
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Template lain", callback_data="t:again")],
    ])
    return (
        "✅ Template siap — <b>Tahan pesan utk salin</b> (template terlalu panjang utk tombol Salin), "
        "lalu tempel ke WhatsApp/Telegram dosen:\n\n"
        f"{info}\n\n"
        f"{html.escape(plain)}\n\n"
        "<i>Ketik /reminder_dosen utk template lain.</i>"
    ), kb


# ---------- picker kelas (grup bertingkat, reuse log._group_picker_classes) ----------

def _plabel(c: sheets.ClassEntry, done: set, delegated: set, today=None) -> str:
    """Label picker: ✅ sudah · 🔄 backup · 🧪 make-up · 🔀 kelas didelegasikan."""
    prefix = "✅ " if sheets.class_done_key(c) in done else ""
    if c.category == "Backup":
        return f"{prefix}🔄 {c.code} — {c.subject} ({c.backup_hari_tanggal})"
    if c.category == "Make-up":
        return f"{prefix}🧪 {c.code} — {c.subject} ({c.backup_hari_tanggal})"
    mon, sun = hlog._week_span(today)
    wd = hlog._class_week_date(c, mon, sun)
    is_del = wd is not None and (c.code.casefold(), wd.strftime("%d/%m/%Y")) in delegated
    mark = "🔀 " if is_del else ""
    return f"{prefix}{mark}{c.code} — {c.subject} ({c.day} {c.time_range})"


def _build_view(classes: list[sheets.ClassEntry], done: set, delegated: set,
                show_done: bool = False, today=None):
    """(text, kb_rows) — sama dgn log._build_class_view, label pakai _plabel (🔀)."""
    week, arrears, done_list = hlog._group_picker_classes(classes, done, today)
    groups = [("📅 MINGGU INI", week), ("⏳ SEBELUMNYA (belum di-log)", arrears)]
    if show_done:
        groups.append(("✅ SUDAH DI-LOG", done_list))
    text_parts = ["1️⃣ Pilih kelas: (⭐ kelas sendiri · 🔀 didelegasikan · 🔄 kamu backup · 🧪 make-up · ✅ sudah)"]
    if not (week or arrears):
        text_parts.append("Tidak ada kelas minggu ini")
    for header, items in groups:
        if items:
            text_parts.append(f"— {header} —")
    text = "\n".join(text_parts)
    kb_rows = []
    idx = 0
    for _, items in groups:
        for c in items:
            kb_rows.append([InlineKeyboardButton(_plabel(c, done, delegated, today), callback_data=f"c:{idx}")])
            idx += 1
    if done_list:
        if show_done:
            kb_rows.append([InlineKeyboardButton("🙈 Sembunyikan sudah di-log", callback_data="vd:0")])
        else:
            kb_rows.append([InlineKeyboardButton(f"👁 ({len(done_list)}) sudah di-log", callback_data="vd:1")])
    kb_rows.append([InlineKeyboardButton("❌ Batal", callback_data="back:cancel")])
    return text, kb_rows


# ---------- entry ----------

async def cmd_reminder_dosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
    # S2 guard: re-entry form yg sama -> jangan reset; handler lain aktif -> blok.
    g = await _guard.guard_entry(update, context, "reminder_dosen_conv", "reminder_dosen")
    if g is not None:
        return g
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    name = users.get(update.effective_chat.id)
    if not name:
        await update.effective_message.reply_text(users.UNREGISTERED_MSG)
        return ConversationHandler.END
    context.user_data["facilitator"] = name
    loading = await update.effective_message.reply_text("⏳ Harap tunggu — ambil jadwal...")
    try:
        personal = await _sheets(context).get_classes(name, include_delegated=True)
        backup = await _sheets(context).get_backup_classes(name)
        makeup = await _sheets(context).get_makeup_classes(name)
    except sheets.SheetsError as exc:
        try: await loading.delete()
        except Exception: pass
        await update.effective_message.reply_text(f"⚠️ {exc}")
        return ConversationHandler.END
    try: await loading.delete()
    except Exception: pass
    classes = personal + backup + makeup
    if not classes:
        await update.effective_message.reply_text(f"Tidak ada kelas untuk {name} (jadwal + backup kosong).")
        return ConversationHandler.END
    try:
        done = await _sheets(context).get_done_by_date(name)
    except Exception:
        done = set()
    try:
        delegated = await _sheets(context).active_backup_keys(name)
    except Exception:
        delegated = set()
    week, arrears, done_list = hlog._group_picker_classes(classes, done)
    ordered = week + arrears + done_list
    context.user_data["p_classes"] = ordered
    context.user_data["p_done"] = done
    context.user_data["p_delegated"] = delegated
    show_done = bool(context.user_data.get("p_show_done"))
    text, kb_rows = _build_view(ordered, done, delegated, show_done)
    context.user_data["p_class_kb"] = kb_rows
    context.user_data["p_class_text"] = text
    await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb_rows))
    return CLASS


# ---------- step 1: pick class ----------

async def toggle_done_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    classes = context.user_data.get("p_classes") or []
    if not classes:
        await q.message.reply_text("Kembali ke awal — kirim /reminder_dosen lagi.")
        return ConversationHandler.END
    context.user_data["p_show_done"] = q.data == "vd:1"
    done = context.user_data.get("p_done") or set()
    delegated = context.user_data.get("p_delegated") or set()
    text, kb_rows = _build_view(classes, done, delegated, context.user_data.get("p_show_done"))
    context.user_data["p_class_kb"] = kb_rows
    context.user_data["p_class_text"] = text
    try:
        await q.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb_rows))
    except Exception:
        await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb_rows))
    return CLASS


async def pick_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split(":")[1])
    classes: list[sheets.ClassEntry] = context.user_data.get("p_classes") or []
    if not 0 <= idx < len(classes):
        await q.message.reply_text("Pilihan kedaluwarsa — kirim /reminder_dosen lagi.")
        return ConversationHandler.END
    c = classes[idx]
    context.user_data["cls"] = c
    context.user_data.pop("p_date", None)
    await q.message.edit_text(
        f"Kelas: <b>{html.escape(c.code)}</b> — {html.escape(c.subject)}\n\n"
        "1️⃣ Pilih jenis template:",
        parse_mode=ParseMode.HTML, reply_markup=_type_kb())
    return TYPE


def _type_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1. Normal (pengingat)", callback_data="t:1"),
         InlineKeyboardButton("2. Backup (digantikan)", callback_data="t:2")],
        [InlineKeyboardButton("3. Reschedule (ubah jadwal)", callback_data="t:3")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="back:class")],
    ])


async def back_to_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    kb_rows = context.user_data.get("p_class_kb")
    if not kb_rows:
        await q.message.reply_text("Kembali ke awal — kirim /reminder_dosen lagi.")
        return ConversationHandler.END
    await q.message.reply_text(context.user_data.get("p_class_text") or "1️⃣ Pilih kelas:",
                               reply_markup=InlineKeyboardMarkup(kb_rows))
    return CLASS


async def back_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Dibatalkan.")
    return ConversationHandler.END


# ---------- step 2: pilih jenis template ----------

def _date_kb(c: sheets.ClassEntry) -> InlineKeyboardMarkup:
    last = sheets.last_date_for_day(c.day)
    nxt = sheets.next_date_for_day(c.day)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📅 {sheets.tanggal_panjang(nxt)} · jadwal berikutnya", callback_data="d:next")],
        [InlineKeyboardButton(f"📅 {sheets.tanggal_panjang(last)} · pertemuan terakhir", callback_data="d:last")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="back:type")],
    ])


async def pick_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    tipe = int(q.data.split(":")[1])
    context.user_data["p_tipe"] = tipe
    c: sheets.ClassEntry = context.user_data["cls"]
    if _needs_date_step(c):
        context.user_data["p_date"] = _date_for(c)  # default berikutnya — bisa ganti di step
        await q.message.reply_text(
            "📅 Tanggal kelasnya? — pilih salah satu (default jadwal berikutnya)",
            reply_markup=_date_kb(c))
        return DATE
    context.user_data["p_date"] = _date_for(c)
    return await _resolve_after_date(q.message, context)


async def pick_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    c: sheets.ClassEntry = context.user_data.get("cls")
    if not c:
        await q.message.reply_text("Pilihan kedaluwarsa — kirim /reminder_dosen lagi.")
        return ConversationHandler.END
    context.user_data["p_date"] = _date_for(c, q.data)
    return await _resolve_after_date(q.message, context)


async def back_to_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("1️⃣ Pilih jenis template:", reply_markup=_type_kb())
    return TYPE


async def _resolve_after_date(message: Message, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Setelah tanggal pasti: prefill data tambahan per tipe, atau tanya manual."""
    tipe = context.user_data["p_tipe"]
    c: sheets.ClassEntry = context.user_data["cls"]
    tgl = context.user_data["p_date"]
    if tipe == T_BACKUP:
        try:
            rec = await _sheets(context).backup_context(c.code, tgl)
        except Exception:
            rec = None
        if rec and (rec.get("awal") or rec.get("pengganti")):
            context.user_data["p_pengganti"] = rec["pengganti"]
            context.user_data["p_fasil_awal"] = rec["awal"]
            return await _sapa_step(message, context)
        # Tidak ada data backup utk kode+tanggal ini — tanya manual.
        context.user_data["p_fasil_awal"] = context.user_data.get("facilitator") or ""
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="back:type")]])
        await message.reply_text("👤 Siapa penggantinya? (nama rekan fasilitator)", reply_markup=kb)
        return PENGGANTI
    if tipe == T_RESCHEDULE:
        try:
            csched = await _sheets(context).cancel_schedule_for(c.code)
        except Exception:
            csched = None
        context.user_data["p_sebelum"] = _jadwal_sebelumnya(c, tgl, csched)
        baru = _prefill_jadwal_baru(c, csched)
        if baru:
            context.user_data["p_jadwal_baru"] = baru
            return await _sapa_step(message, context)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="back:type")]])
        await message.reply_text(
            "📝 Isi jadwal baru: (contoh: Senin, 15 September 2026, 13.00 - 15.30 WIB)",
            reply_markup=kb)
        return JADWAL_BARU
    return await _sapa_step(message, context)


async def enter_pengganti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["p_pengganti"] = update.message.text.strip()
    return await _sapa_step(update.message, context)


async def enter_jadwal_baru(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["p_jadwal_baru"] = update.message.text.strip()
    return await _sapa_step(update.message, context)


# ---------- step 3: Bu/Pak + final ----------

def _sapa_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👩‍🏫 Bu", callback_data="s:bu"),
         InlineKeyboardButton("👨‍🏫 Pak", callback_data="s:pak")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="back:type")],
    ])


async def _sapa_step(message: Message, context: ContextTypes.DEFAULT_TYPE) -> int:
    await message.reply_text("Bu atau Pak utk sapaan dosen? 👇", reply_markup=_sapa_kb())
    return SAPA


async def pick_sapa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    bu_pak = "Bu" if q.data == "s:bu" else "Pak"
    c: sheets.ClassEntry = context.user_data["cls"]
    tgl = context.user_data["p_date"]
    sapa = _sapaan_wib()
    tipe = context.user_data["p_tipe"]
    if tipe == T_NORMAL:
        plain = render_normal(c, sapa, bu_pak, tgl)
    elif tipe == T_BACKUP:
        plain = render_backup(c, sapa, bu_pak, tgl,
                              context.user_data["p_pengganti"], context.user_data["p_fasil_awal"])
    else:
        plain = render_reschedule(c, sapa, bu_pak, tgl,
                                  context.user_data["p_sebelum"], context.user_data["p_jadwal_baru"])
    final, kb = _final_message(plain, c.subject, c.code)
    try:
        await q.message.edit_text(final, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        await q.message.reply_text(final, parse_mode=ParseMode.HTML, reply_markup=kb)
    facilitator = context.user_data.get("facilitator") or ""
    usage.log(update.effective_chat.id, facilitator, "reminder_dosen", c.code, tipe=f"t{tipe}")
    return ConversationHandler.END


async def again(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Tombol '🔄 Template lain' — langsung muat ulang picker (conv sudah END)."""
    q = update.callback_query
    await q.answer()
    return await cmd_reminder_dosen(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text("Form dibatalkan.")
    return ConversationHandler.END


async def timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text("⏱ Form timeout (1 jam). Kirim /reminder_dosen untuk mulai lagi.")
    return ConversationHandler.END


def register(app: Application, cfg: Config) -> None:
    global REMINDER_DOSEN_CONV
    conv = ConversationHandler(
        entry_points=[CommandHandler("reminder_dosen", cmd_reminder_dosen),
                      CallbackQueryHandler(again, pattern=r"^t:again$")],
        states={
            CLASS: [CallbackQueryHandler(pick_class, pattern=r"^c:\d+$"),
                    CallbackQueryHandler(toggle_done_view, pattern=r"^vd:[01]$"),
                    CallbackQueryHandler(back_cancel, pattern=r"^back:cancel$")],
            TYPE: [CallbackQueryHandler(pick_type, pattern=r"^t:[123]$"),
                   CallbackQueryHandler(back_to_class, pattern=r"^back:class$")],
            DATE: [CallbackQueryHandler(pick_date, pattern=r"^d:(next|last)$"),
                   CallbackQueryHandler(back_to_type, pattern=r"^back:type$")],
            PENGGANTI: [MessageHandler(_SKIP_FILTER, enter_pengganti),
                        CallbackQueryHandler(back_to_type, pattern=r"^back:type$")],
            JADWAL_BARU: [MessageHandler(_SKIP_FILTER, enter_jadwal_baru),
                          CallbackQueryHandler(back_to_type, pattern=r"^back:type$")],
            SAPA: [CallbackQueryHandler(pick_sapa, pattern=r"^s:(bu|pak)$"),
                   CallbackQueryHandler(back_to_type, pattern=r"^back:type$")],
            # v22: on_timeout param removed — next update after timeout lands in TIMEOUT state
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, timeout)],
        },
        fallbacks=[CommandHandler("cancel", cancel),
                   CommandHandler("start", cancel),
                   CommandHandler("reminder_dosen", cmd_reminder_dosen)],
        conversation_timeout=_TIMEOUT,
        name="reminder_dosen_conv",
        allow_reentry=True,
    )
    REMINDER_DOSEN_CONV = conv
    _guard.register_conv("reminder_dosen_conv", conv)
    app.add_handler(conv)