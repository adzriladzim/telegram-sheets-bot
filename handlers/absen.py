"""/absen — rekap kehadiran mahasiswa ke sheet Absen."""
from __future__ import annotations

import html
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

import sheets
import users
from config import Config

log = logging.getLogger(__name__)

KODE, PERTEMUAN, METHOD, INPUT, CHECKLIST, STATUS, CONFIRM = range(7)
_TIMEOUT = 60*60

def _sheets(ctx): return ctx.bot_data["sheets"]

async def cmd_absen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from telegram.constants import ChatAction
    if update.callback_query:
        await update.callback_query.answer()
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    name = users.get(update.effective_chat.id)
    if not name:
        await update.effective_message.reply_text(users.UNREGISTERED_MSG)
        return ConversationHandler.END
    loading = await update.effective_message.reply_text("⏳ Ambil daftar kelas...")
    try:
        personal, backup, makeup = await _sheets(context).get_all_loggable_classes(name)
        my_classes = personal + backup + makeup
        backup_kodes = {c.code for c in backup}
        makeup_kodes = {c.code for c in makeup}
        my_kodes = {c.code for c in my_classes}
    except sheets.SheetsError as e:
        try: await loading.delete()
        except Exception: pass
        await update.effective_message.reply_text(f"⚠️ {e}")
        return ConversationHandler.END
    try: await loading.delete()
    except Exception: pass
    if not my_kodes:
        await update.effective_message.reply_text("Tidak ada kelas di Jadwal untuk absen.")
        return ConversationHandler.END
    from sheets import today_day_wib
    today = today_day_wib()
    today_kodes = [c.code for c in my_classes if c.day == today]
    # Unique preserve order
    seen=set(); my_today=[]
    for k in today_kodes:
        if k not in seen:
            seen.add(k); my_today.append(k)
    my_other = [k for k in my_kodes if k not in set(my_today)]
    context.user_data["absen_today"] = today
    context.user_data["absen_my_today"] = my_today
    context.user_data["absen_my_other"] = my_other
    context.user_data["absen_backup"] = backup_kodes
    context.user_data["absen_makeup"] = makeup_kodes
    kb = _kode_kb(my_today, my_other, today, backup_kodes, makeup_kodes)
    await update.effective_message.reply_text("1️⃣ Pilih Kode Kelas: (⭐ sendiri · 🔄 backup · 🧪 make-up)", reply_markup=kb)
    context.user_data["absen_kodes"] = list(my_kodes)
    return KODE


def _kode_kb(my_today, my_other, today, backup=None, makeup=None) -> InlineKeyboardMarkup:
    backup = backup or set()
    makeup = makeup or set()
    kb = []
    if my_today:
        kb.append([InlineKeyboardButton(f"— Hari Ini ({today}) —", callback_data="abk:header")])
        for k in my_today:
            mark = "🧪" if k in makeup else ("🔄" if k in backup else "⭐")
            kb.append([InlineKeyboardButton(f"{mark} {k} (hari ini)", callback_data=f"abk:{k}")])
    if my_other:
        kb.append([InlineKeyboardButton("— Kelas Lain Saya —", callback_data="abk:header2")])
        for k in my_other[:15]:
            mark = "🧪" if k in makeup else ("🔄" if k in backup else "⭐")
            kb.append([InlineKeyboardButton(f"{mark} {k}", callback_data=f"abk:{k}")])
    kb.append([InlineKeyboardButton("⌨️ Ketik Kode Lain", callback_data="abk:manual")])
    kb.append([InlineKeyboardButton("❌ Batal", callback_data="abk:cancel")])
    return InlineKeyboardMarkup(kb)


async def back_to_kode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    my_today = context.user_data.get("absen_my_today", [])
    my_other = context.user_data.get("absen_my_other", [])
    today = context.user_data.get("absen_today", "")
    backup = context.user_data.get("absen_backup", set())
    makeup = context.user_data.get("absen_makeup", set())
    await q.message.reply_text("1️⃣ Pilih Kode Kelas:", reply_markup=_kode_kb(my_today, my_other, today, backup, makeup))
    return KODE


async def back_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Dibatalkan.")
    return ConversationHandler.END


def _pertemuan_prompt_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="abk:back_kode")]])

def _fmt_pertemuan(per) -> str:
    """'3' utk single, '3 dan 4' utk gabungan (list)."""
    if isinstance(per, (list, tuple)):
        return " dan ".join(str(p) for p in per)
    return str(per)


def _method_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⌨️ Ketik NIM", callback_data="abm:nim"), InlineKeyboardButton("☑️ Checklist Nama", callback_data="abm:nama")],
        [InlineKeyboardButton("🔒 Upload Foto (Segera)", callback_data="abm:foto")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="abm:back")],
    ])


def _status_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("S Onsite", callback_data="abs:S"), InlineKeyboardButton("O Online", callback_data="abs:O")],
        [InlineKeyboardButton("A Absent", callback_data="abs:A"), InlineKeyboardButton("I Izin", callback_data="abs:I")],
        [InlineKeyboardButton("SF", callback_data="abs:SF"), InlineKeyboardButton("OF", callback_data="abs:OF")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="abs:back")],
    ])


async def back_to_pertemuan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("2️⃣ Pertemuan ke-? (1-16 · gabungan: 3 dan 4 / 3-4)", reply_markup=_pertemuan_prompt_kb())
    return PERTEMUAN

async def pick_kode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    kode = q.data.split(":",1)[1]
    if kode.startswith("header"):
        return KODE
    if kode == "manual":
        await q.message.reply_text("Ketik Kode Kelas (contoh: Arch1):")
        return KODE
    if kode == "cancel":
        return await back_cancel(update, context)
    context.user_data["absen_kode"] = kode
    await q.message.edit_text(f"Kode: <b>{html.escape(kode)}</b>", parse_mode=ParseMode.HTML)
    await q.message.reply_text("2️⃣ Pertemuan ke-? (1-16 · gabungan: 3 dan 4 / 3-4)", reply_markup=_pertemuan_prompt_kb())
    return PERTEMUAN

async def enter_kode_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    kode = update.message.text.strip()
    context.user_data["absen_kode"] = kode
    await update.effective_message.reply_text(f"Kode: {kode}\n2️⃣ Pertemuan ke-? (1-16 · gabungan: 3 dan 4 / 3-4)", reply_markup=_pertemuan_prompt_kb())
    return PERTEMUAN

async def enter_pertemuan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    import re
    t = update.message.text.strip()
    m = re.match(r"^(\d{1,2})\s*(?:dan|[-&])\s*(\d{1,2})$", t, re.IGNORECASE)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if not (1 <= a <= 16 and 1 <= b <= 16):
            await update.effective_message.reply_text("Masukkan 1-16")
            return PERTEMUAN
        if a == b:
            await update.effective_message.reply_text("Sesi gabungan butuh dua nomor berbeda (mis: 3 dan 4).")
            return PERTEMUAN
        context.user_data["absen_pertemuan"] = [a, b]
    elif t.isdigit() and 1 <= int(t) <= 16:
        context.user_data["absen_pertemuan"] = int(t)
    else:
        await update.effective_message.reply_text("Masukkan 1-16 (gabungan: 3 dan 4 / 3-4)")
        return PERTEMUAN
    await update.effective_message.reply_text("3️⃣ Input via?", reply_markup=_method_kb())
    return METHOD

async def pick_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    m = q.data.split(":",1)[1]
    if m == "back":
        return await back_to_pertemuan(update, context)
    if m == "foto":
        await q.message.reply_text(
            "🔒 Fitur foto segera hadir — pakai ⌨️ NIM atau ☑️ Checklist dulu ya.",
            reply_markup=_method_kb())
        return METHOD
    context.user_data["absen_method"] = m
    back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="abi:back")]])
    if m == "nim":
        context.user_data["input_from"] = "method"
        await q.message.reply_text(
            "Ketik NIM / Nama Zoom (pisahkan koma).\n"
            "Contoh NIM: 26111600029, 26111600004\n"
            "Contoh Nama Zoom: 029_Ahmad Maulana_If",
            reply_markup=back_kb)
        return INPUT
    else:
        # Checklist Nama
        kode = context.user_data["absen_kode"]
        context.user_data["input_from"] = "checklist"
        await q.message.reply_text("⏳ Ambil daftar mahasiswa...")
        try:
            students = await _sheets(context).list_students(kode)
        except sheets.SheetsError as e:
            await q.message.reply_text(f"⚠️ {e}")
            return METHOD
        if not students:
            await q.message.reply_text("Daftar kosong, ketik manual:")
            return INPUT
        context.user_data["absen_students"] = students
        context.user_data["absen_selected"] = set()
        context.user_data["absen_manual"] = []
        context.user_data["absen_page"] = 0
        return await _show_checklist(q.message, context)

PAGE_SIZE = 25

def _checklist_text(context) -> str:
    sel = len(context.user_data.get("absen_selected", set()))
    man = len(context.user_data.get("absen_manual", []))
    total = len(context.user_data.get("absen_students", []))
    extra = f" + {man} manual" if man else ""
    page = context.user_data.get("absen_page", 0) + 1
    pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    return f"Checklist ({sel} terpilih{extra}) — {total} mhs, hal {page}/{pages}. Tap nama untuk toggle:"

async def _show_checklist(msg, context):
    students = context.user_data["absen_students"]
    selected = context.user_data["absen_selected"]
    page = context.user_data.get("absen_page", 0)
    kb = _build_checklist_kb(students, selected, page)
    await msg.reply_text(_checklist_text(context), reply_markup=kb)
    return CHECKLIST

def _build_checklist_kb(students, selected, page=0):
    kb = []
    start = page * PAGE_SIZE
    for i in range(start, min(start + PAGE_SIZE, len(students))):
        nim, nama, mode = students[i]
        prefix = "✅" if nim in selected else "⬜"
        label = f"{prefix} {nama[:20]}"
        if mode:
            label += f" [{mode[:10]}]"
        kb.append([InlineKeyboardButton(label, callback_data=f"chk:{i}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("‹ Prev", callback_data="chk:prev"))
    if start + PAGE_SIZE < len(students):
        nav.append(InlineKeyboardButton("Next ›", callback_data="chk:next"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("✅ Selesai", callback_data="chk:done"), InlineKeyboardButton("➕ Tambah Manual", callback_data="chk:add")])
    kb.append([InlineKeyboardButton("◀️ Kembali", callback_data="chk:back")])
    return InlineKeyboardMarkup(kb)


async def back_to_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("3️⃣ Input via?", reply_markup=_method_kb())
    return METHOD


async def back_to_input_src(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Back from manual text input: checklist-add returns to checklist, else to method."""
    q = update.callback_query
    await q.answer()
    if context.user_data.get("input_from") == "checklist" and "absen_students" in context.user_data:
        students = context.user_data["absen_students"]
        sel = context.user_data.get("absen_selected", set())
        kb = _build_checklist_kb(students, sel)
        await q.message.reply_text(_checklist_text(context), reply_markup=kb)
        return CHECKLIST
    return await back_to_method(update, context)


async def back_to_status_src(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Back from status: checklist-origin returns to checklist, else to method."""
    q = update.callback_query
    await q.answer()
    if context.user_data.get("status_from") == "checklist" and "absen_students" in context.user_data:
        students = context.user_data["absen_students"]
        sel = context.user_data.get("absen_selected", set())
        kb = _build_checklist_kb(students, sel)
        await q.message.reply_text(_checklist_text(context), reply_markup=kb)
        return CHECKLIST
    return await back_to_method(update, context)

async def toggle_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    data = q.data.split(":",1)[1]
    if data == "done":
        sel = context.user_data.get("absen_selected", set())
        man = context.user_data.get("absen_manual", [])
        if not sel and not man:
            await q.answer("Pilih minimal 1", show_alert=True)
            return CHECKLIST
        context.user_data["absen_identifiers"] = list(sel) + list(man)
        context.user_data["status_from"] = "checklist"
        await q.message.reply_text(f"Terdeteksi {len(context.user_data['absen_identifiers'])} mahasiswa ({len(sel)} checklist + {len(man)} manual). Pilih status:", reply_markup=_status_kb())
        return STATUS
    if data == "add":
        back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="abi:back")]])
        await q.message.reply_text(
            "Ketik Nama/NIM di luar list (pisahkan koma).\n"
            "Nama Zoom juga bisa: 029_Ahmad Maulana_If",
            reply_markup=back_kb)
        return INPUT
    if data == "back":
        return await back_to_method(update, context)
    if data in ("prev", "next"):
        total = len(context.user_data.get("absen_students", []))
        pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
        page = context.user_data.get("absen_page", 0)
        page = max(0, min(pages - 1, page + (1 if data == "next" else -1)))
        context.user_data["absen_page"] = page
        students = context.user_data["absen_students"]
        sel = context.user_data["absen_selected"]
        kb = _build_checklist_kb(students, sel, page)
        try:
            await q.message.edit_reply_markup(reply_markup=kb)
            await q.message.edit_text(_checklist_text(context), reply_markup=kb)
        except Exception:
            await q.message.edit_text(_checklist_text(context), reply_markup=kb)
        return CHECKLIST
    idx = int(data)
    students = context.user_data.get("absen_students") or []
    if not 0 <= idx < len(students):
        await q.message.reply_text("Pilihan kedaluwarsa — kirim /absen lagi.")
        return ConversationHandler.END
    nim = students[idx][0]
    sel = context.user_data["absen_selected"]
    if nim in sel: sel.remove(nim)
    else: sel.add(nim)
    # Edit in place — no reload
    kb = _build_checklist_kb(students, sel, context.user_data.get("absen_page", 0))
    try:
        await q.message.edit_reply_markup(reply_markup=kb)
        await q.message.edit_text(_checklist_text(context), reply_markup=kb)
    except Exception:
        # Fallback if edit fails
        await q.message.edit_text(_checklist_text(context), reply_markup=kb)
    return CHECKLIST

async def enter_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.photo:
        await update.effective_message.reply_text(
            "🔒 Fitur foto segera hadir — pakai ⌨️ NIM atau ☑️ Checklist dulu ya.\n"
            "Ketik NIM/Nama manual (pisahkan koma):")
        return INPUT
    text = update.message.text.strip()
    import re
    parts = [p.strip() for p in re.split(r"[,\\n]+", text) if p.strip()]
    if not parts:
        await update.effective_message.reply_text("Masukkan minimal 1 NIM/Nama")
        return INPUT
    # If coming from checklist (Tambah Manual), append and go BACK to checklist
    if "absen_students" in context.user_data:
        man = context.user_data.get("absen_manual", [])
        man.extend(parts)
        context.user_data["absen_manual"] = man
        sel = len(context.user_data.get("absen_selected", set()))
        await update.effective_message.reply_text(f"➕ Tambah {len(parts)} manual — checklist {sel} + manual {len(man)}. Lanjut klik yang belum, lalu Selesai.")
        kb = _build_checklist_kb(context.user_data["absen_students"], context.user_data.get("absen_selected", set()))
        await update.effective_message.reply_text(_checklist_text(context), reply_markup=kb)
        return CHECKLIST
    context.user_data["absen_identifiers"] = parts
    context.user_data["status_from"] = "input"
    await update.effective_message.reply_text(f"Terdeteksi {len(context.user_data['absen_identifiers'])} mahasiswa. Pilih status:", reply_markup=_status_kb())
    return STATUS

async def pick_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    status = q.data.split(":",1)[1]
    context.user_data["absen_status"] = status
    kode = context.user_data["absen_kode"]
    per = context.user_data["absen_pertemuan"]
    ids = context.user_data["absen_identifiers"]
    per_fmt = _fmt_pertemuan(per) + (" (gabungan)" if isinstance(per, (list, tuple)) else "")
    txt = f"📋 <b>Konfirmasi Absen:</b>\n• Kode: {html.escape(kode)}\n• Pertemuan: {per_fmt}\n• Status: {status}\n• Jumlah: {len(ids)}\n• NIM/Nama: {', '.join(html.escape(x) for x in ids[:5])}{' ...' if len(ids)>5 else ''}\n\nSubmit?"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Submit", callback_data="abc:ok"), InlineKeyboardButton("❌ Batal", callback_data="abc:no")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="abc:back")],
    ])
    await q.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
    return CONFIRM


async def back_to_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    n = len(context.user_data.get("absen_identifiers", []))
    await q.message.reply_text(f"Pilih status ({n} mahasiswa):", reply_markup=_status_kb())
    return STATUS

_REQUIRED_KEYS = ("absen_kode", "absen_pertemuan", "absen_identifiers", "absen_status")

async def confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    # Stale-session guard: conversation may have ended (timeout / done elsewhere /
    # previous double-tap). Indexing user_data without the keys raises KeyError.
    if any(k not in context.user_data for k in _REQUIRED_KEYS):
        try: await q.edit_message_reply_markup(reply_markup=None)
        except Exception: pass
        await q.message.reply_text("Sesi absen sudah selesai — kirim /absen untuk yang baru.")
        return ConversationHandler.END
    # Per-chat lock (see handlers/log.py confirm_cb).
    async with _sheets(context).for_chat(update.effective_chat.id):
        if q.data.endswith(":no"):
            try: await q.edit_message_reply_markup(reply_markup=None)
            except Exception: pass
            await q.message.reply_text("Dibatalkan.")
            return ConversationHandler.END
        kode = context.user_data["absen_kode"]
        per = context.user_data["absen_pertemuan"]
        ids = context.user_data["absen_identifiers"]
        status = context.user_data["absen_status"]
        busy = await q.message.reply_text("⏳ Menyimpan absen ke sheet...")
        try:
            pengisi = users.get(update.effective_chat.id) or ""
            res = await _sheets(context).update_absen(kode, per, ids, status, pengisi=pengisi)
        except sheets.SheetsError as e:
            try: await busy.delete()
            except Exception: pass
            await q.message.reply_text(f"⚠️ Gagal: {e}")
            return CONFIRM
        try: await busy.delete()
        except Exception: pass
        n = res["updated"]
        extra = ""
        if res["ambiguous"]:
            amb = "; ".join(f"{k}→{', '.join(v[:2])}" for k, v in list(res["ambiguous"].items())[:3])
            extra += f"\n⚠️ Ambigu (pakai NIM penuh): {amb}"
        if res["unmatched"]:
            extra += f"\n❌ Tak ketemu: {', '.join(res['unmatched'][:5])}"
        nama_pengisi = res.get("pengisi") or "-"
        await q.message.reply_text(f"✅ Absen tercatat: {kode} pertemuan {_fmt_pertemuan(per)} → {n} mahasiswa status {status}"
                                   f"\n👤 Pengisi: {nama_pengisi}{extra}")
        # Drop the buttons so a second tap of ✅ Submit can't re-enter and blow up
        # on cleared user_data (double-tap -> KeyError -> 2x "kesalahan internal").
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        # Log usage
        import usage
        try: usage.log(update.effective_chat.id, nama_pengisi, "absen", kode,
                       pertemuan=per, status=status, jumlah=len(ids))
        except Exception: pass
        context.user_data.clear()
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text("Dibatalkan.")
    return ConversationHandler.END

def register(app: Application, cfg: Config) -> None:
    conv = ConversationHandler(
        entry_points=[CommandHandler("absen", cmd_absen),
                      CallbackQueryHandler(cmd_absen, pattern=r"^go:absen$")],
        states={
            KODE: [CallbackQueryHandler(pick_kode, pattern=r"^abk:"), MessageHandler(filters.TEXT & ~filters.COMMAND, enter_kode_manual)],
            PERTEMUAN: [CallbackQueryHandler(back_to_kode, pattern=r"^abk:back_kode$"), MessageHandler(filters.TEXT & ~filters.COMMAND, enter_pertemuan)],
            METHOD: [CallbackQueryHandler(pick_method, pattern=r"^abm:")],
            INPUT: [CallbackQueryHandler(back_to_input_src, pattern=r"^abi:back$"), MessageHandler(filters.TEXT & ~filters.COMMAND, enter_input), MessageHandler(filters.PHOTO, enter_input)],
            CHECKLIST: [CallbackQueryHandler(toggle_check, pattern=r"^chk:")],
            STATUS: [CallbackQueryHandler(back_to_status_src, pattern=r"^abs:back$"), CallbackQueryHandler(pick_status, pattern=r"^abs:")],
            CONFIRM: [CallbackQueryHandler(back_to_status, pattern=r"^abc:back$"), CallbackQueryHandler(confirm_cb, pattern=r"^abc:")],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, cancel)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=_TIMEOUT, name="absen_conv",
        allow_reentry=True,
    )
    app.add_handler(conv)
