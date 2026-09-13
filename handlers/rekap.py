"""/rekap — rekap kehadiran fasil per kelas (spreadsheet Rekap Batch 5)."""
from __future__ import annotations

import logging
import re

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

CLASS, MEETING, TIPE, SESI, PERAN, BUKTI, CONFIRM = range(7)
_TIMEOUT = 60 * 60

_SKIP_FILTER = filters.TEXT & ~filters.COMMAND


def _sheets(context: ContextTypes.DEFAULT_TYPE) -> sheets.SheetsClient:
    return context.bot_data["sheets"]


def _first_num(s: str) -> int:
    m = re.search(r"\d+", s or "")
    return int(m.group(0)) if m else 1


# ---------- step 1: pick class ----------

async def cmd_rekap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
    from telegram.constants import ChatAction
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    facilitator = users.get(update.effective_chat.id)
    if not facilitator:
        await update.effective_message.reply_text(users.UNREGISTERED_MSG)
        return ConversationHandler.END
    context.user_data["facilitator"] = facilitator
    loading = await update.effective_message.reply_text("⏳ Ambil jadwal + tab rekap...")
    try:
        personal, backup = await _sheets(context).get_all_loggable_classes(facilitator)
        tab = await _sheets(context).find_rekap_tab(facilitator)
    except sheets.SheetsError as exc:
        try: await loading.delete()
        except Exception: pass
        await update.effective_message.reply_text(f"⚠️ {exc}")
        return ConversationHandler.END
    try: await loading.delete()
    except Exception: pass
    classes = personal + backup
    if not classes:
        await update.effective_message.reply_text(f"Tidak ada kelas untuk {facilitator}.")
        return ConversationHandler.END
    context.user_data["classes"] = classes
    context.user_data["rekap_tab"] = tab
    try:
        complete, incomplete = await _sheets(context).get_rekap_status(facilitator)
    except Exception:
        complete, incomplete = set(), set()
    kb_rows = []
    for i, c in enumerate(classes):
        if c.category == "Backup" and c.backup_hari_tanggal:
            try:
                from handlers.log import _parse_backup_date
                last_cmp = _parse_backup_date(c.backup_hari_tanggal)
                last_disp = c.backup_hari_tanggal
            except Exception:
                last_cmp = last_disp = c.backup_hari_tanggal
        else:
            last_cmp = sheets.last_date_for_day(c.day)
            last_disp = sheets.tanggal_panjang(last_cmp)
        key = (c.code.casefold(), last_cmp)
        key2 = (c.code.casefold(), last_disp)
        if key in complete or key2 in complete:
            prefix = "✅ "
        elif key in incomplete or key2 in incomplete:
            prefix = "⚠️ "
        else:
            prefix = ""
        if c.category == "Backup":
            label = f"{prefix}🔄 {c.code} — {c.subject} ({c.backup_hari_tanggal})"
        else:
            label = f"{prefix}{c.code} — {c.subject} ({c.day} {c.time_range})"
        kb_rows.append([InlineKeyboardButton(label, callback_data=f"rkc:{i}")])
    kb_rows.append([InlineKeyboardButton("❌ Batal", callback_data="rkb:cancel")])
    context.user_data["class_kb"] = kb_rows
    await update.effective_message.reply_text(
        "1️⃣ Pilih kelas: (✅ = lengkap, ⚠️ = rumpang/lengkapi)", reply_markup=InlineKeyboardMarkup(kb_rows))
    return CLASS


def _tanggal_keys(context, c) -> list:
    """Accepted tanggal strings (dd/mm/yyyy + panjang) for this class's last schedule."""
    if c.category == "Backup" and c.backup_hari_tanggal:
        try:
            from handlers.log import _parse_backup_date
            cmpd = _parse_backup_date(c.backup_hari_tanggal)
        except Exception:
            cmpd = c.backup_hari_tanggal
        return [cmpd, c.backup_hari_tanggal.strip()]
    cmpd = sheets.last_date_for_day(c.day)
    return [cmpd, sheets.tanggal_panjang(cmpd)]


async def pick_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split(":")[1])
    c = context.user_data["classes"][idx]
    context.user_data["cls"] = c
    await q.message.edit_text(f"Kelas: <b>{c.code}</b> — {c.subject}\n\n", parse_mode=ParseMode.HTML)
    # Cek baris rumpang/lengkap untuk tanggal ini — tawarkan lengkapi, jangan dobel
    tab = context.user_data.get("rekap_tab", "")
    try:
        st = await _sheets(context).rekap_row_status(tab, c.code, _tanggal_keys(context, c))
    except Exception:
        st = {"state": "none"}
    if st.get("state") == "complete":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Tetap buat baru", callback_data="rknew:go")],
            [InlineKeyboardButton("◀️ Kembali", callback_data="rkb:class")],
        ])
        await q.message.reply_text(
            f"ℹ️ {c.code} tanggal ini <b>sudah lengkap</b> di Rekap. Buat baris baru?", parse_mode=ParseMode.HTML, reply_markup=kb)
        return CLASS
    if st.get("state") == "incomplete":
        context.user_data["fix_row"] = st["row"]
        context.user_data["fix_gaps"] = st["gaps"]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🧩 Lengkapi baris ini", callback_data="rkfix:yes")],
            [InlineKeyboardButton("➕ Buat baru", callback_data="rkfix:new")],
            [InlineKeyboardButton("◀️ Kembali", callback_data="rkb:class")],
        ])
        await q.message.reply_text(
            f"⚠️ {c.code} tanggal ini <b>belum lengkap</b> (kurang: {', '.join(st['gaps'])}). Lengkapi langsung?",
            parse_mode=ParseMode.HTML, reply_markup=kb)
        return CLASS
    return await _begin_meeting(q.message, context)


async def _begin_meeting(msg, context) -> int:
    c = context.user_data["cls"]
    wait = await msg.reply_text("⏳ Cari pertemuan terakhir...")
    try:
        last, nxt, nxt2 = await _sheets(context).get_next_meeting(c.code)
    except Exception:
        last, nxt, nxt2 = "", "1", "1 dan 2"
    try: await wait.delete()
    except Exception: pass
    context.user_data["suggest"] = (last, nxt, nxt2)
    context.user_data["meeting"] = nxt  # auto-sync dari Zoom Record, tanpa tanya
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Online", callback_data="rkt:o"),
         InlineKeyboardButton("🏫 On-site", callback_data="rkt:s")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="rkb:meeting")],
    ])
    auto_note = f"(auto pertemuan {nxt} dari Zoom Record" + (f", terakhir {last}" if last else "") + ")"
    await msg.reply_text(f"2️⃣ Pertemuan: <b>{nxt}</b> {auto_note}\n3️⃣ Tipe kelas:", parse_mode=ParseMode.HTML, reply_markup=kb)
    return TIPE


async def _begin_meeting_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    context.user_data.pop("fix_row", None)
    return await _begin_meeting(q.message, context)


async def fix_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data.endswith(":new"):
        context.user_data.pop("fix_row", None)
        context.user_data.pop("fix_gaps", None)
        return await _begin_meeting(q.message, context)
    # Lengkapi: prefill dari baris sheet, arahkan ke field yang kurang
    c = context.user_data["cls"]
    tab = context.user_data.get("rekap_tab", "")
    row_idx = context.user_data.get("fix_row")
    try:
        vals = await _sheets(context).rekap_row_values(tab, row_idx)
    except Exception:
        return await _begin_meeting(q.message, context)
    def cell(i):
        return vals[i].strip() if len(vals) > i else ""
    # Prefill: H=7 pertemuan, I=8 tipe, J=9 sesi, K=10 peran
    if cell(7): context.user_data["meeting"] = cell(7)
    if cell(8): context.user_data["tipe"] = cell(8)
    if cell(9): context.user_data["sesi"] = cell(9)
    if cell(10): context.user_data["peran"] = cell(10)
    gaps = context.user_data.get("fix_gaps", [])
    await q.message.reply_text(f"🧩 Melengkapi baris {row_idx} ({c.code}).")
    if not context.user_data.get("meeting"):
        return await _begin_meeting(q.message, context)
    if not context.user_data.get("tipe"):
        return await _ask_tipe(q.message, context)
    if not context.user_data.get("sesi"):
        await q.message.reply_text("4️⃣ Sesi kelas? (atau ketik manual)", reply_markup=_sesi_kb())
        return SESI
    if not context.user_data.get("bukti") and "Bukti" in gaps:
        return await _ask_bukti(q.message, context)
    return await _confirm_fix(q.message, context)


async def _confirm_fix(message: Message, context: ContextTypes.DEFAULT_TYPE) -> int:
    c = context.user_data["cls"]
    tanggal = _tanggal_kelas(context, c)
    meeting = context.user_data.get("meeting", "")
    wait = await message.reply_text("⏳ Hitung kehadiran (Absen + Feedback)...")
    counts, fb = None, None
    try:
        counts = await _sheets(context).absen_counts(c.code, _first_num(meeting))
    except Exception:
        counts = None
    try:
        fb = await _sheets(context).feedback_counts(c.code, meeting, c.lecturer, (counts or {}).get("prodi", ""))
    except Exception:
        fb = None
    try: await wait.delete()
    except Exception: pass
    if counts and counts["total"]:
        o, p, r = str(counts["total"]), str(counts["hadir"]), str(counts["tidak"])
        if fb is not None:
            qf, s = str(fb["q"]), str(max(counts["total"] - fb["q"], 0))
        else:
            qf, s = "", ""
    else:
        o = p = qf = r = s = ""
    context.user_data["counts"] = (o, p, qf, r, s)
    rec = _build_base(context, c, tanggal)
    rec.total, rec.hadir, rec.feedback, rec.tidak, rec.belum = o, p, qf, r, s
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Submit (update baris)", callback_data="rkx:ok"),
         InlineKeyboardButton("❌ Batal", callback_data="rkx:no")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="rkb:bukti")],
    ])
    await message.reply_text(
        f"📋 <b>Lengkapi baris {context.user_data.get('fix_row')}:</b>\n"
        f"• Total/Hadir/Feedback: {o or '-'}/{p or '-'}/{qf or '-'}",
        parse_mode=ParseMode.HTML, reply_markup=kb)
    return CONFIRM


# ---------- step 2: meeting ----------

async def pick_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    val = q.data.split(":", 1)[1]
    if val == "manual":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="rkb:class")]])
        await q.message.reply_text("Ketik pertemuan (contoh: 3 atau '3 dan 4'):", reply_markup=kb)
        return MEETING
    context.user_data["meeting"] = val
    return await _ask_tipe(q.message, context)


async def enter_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    nums = re.findall(r"\d+", text)
    if not nums or not all(1 <= int(n) <= 99 for n in nums):
        await update.message.reply_text("Masukkan pertemuan 1–99 (bisa '3 dan 4').")
        return MEETING
    context.user_data["meeting"] = text
    return await _ask_tipe(update.message, context)


async def _ask_tipe(msg, context) -> int:
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Online", callback_data="rkt:o"),
         InlineKeyboardButton("🏫 On-site", callback_data="rkt:s")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="rkb:meeting")],
    ])
    await msg.reply_text("3️⃣ Tipe kelas:", reply_markup=kb)
    return TIPE


# ---------- step 3: tipe ----------

async def pick_tipe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    context.user_data["tipe"] = "Online" if q.data.endswith(":o") else "On-site"
    await q.message.reply_text("4️⃣ Sesi kelas? (atau ketik manual)", reply_markup=_sesi_kb())
    return SESI


# ---------- step 4: sesi ----------

SESI_OPTS = ["Kelas Biasa", "Guest Lecture", "Workshop/E-Lab", "Lainnya"]


def _sesi_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(s, callback_data=f"rks:{i}")] for i, s in enumerate(SESI_OPTS)]
    rows.append([InlineKeyboardButton("◀️ Kembali", callback_data="rkb:tipe")])
    return InlineKeyboardMarkup(rows)


async def pick_sesi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    context.user_data["sesi"] = SESI_OPTS[int(q.data.split(":")[1])]
    return await _ask_peran(q.message, context)


async def enter_sesi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["sesi"] = update.message.text.strip()
    return await _ask_peran(update.message, context)


# ---------- step 5: peran ----------

PERAN_OPTS = ["Fasilitator Kelas", "Moderator Guest Lecture", "Backup Fasil"]


def _peran_kb(suggest: str = "") -> InlineKeyboardMarkup:
    rows = []
    for i, p in enumerate(PERAN_OPTS):
        label = f"✅ {p}" if p == suggest else p
        rows.append([InlineKeyboardButton(label, callback_data=f"rkp:{i}")])
    rows.append([InlineKeyboardButton("◀️ Kembali", callback_data="rkb:sesi")])
    return InlineKeyboardMarkup(rows)


async def _ask_peran(msg, context) -> int:
    c = context.user_data.get("cls")
    suggest = "Backup Fasil" if (c and c.category == "Backup") else "Fasilitator Kelas"
    await msg.reply_text(f"5️⃣ Peran? (saran: {suggest})", reply_markup=_peran_kb(suggest))
    return PERAN


async def pick_peran(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    context.user_data["peran"] = PERAN_OPTS[int(q.data.split(":")[1])]
    return await _ask_bukti(q.message, context)


async def _ask_bukti(msg, context) -> int:
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="rkb:peran")]])
    await msg.reply_text(
        "6️⃣ Bukti kehadiran? Kirim **foto** screenshot, paste **link Drive**, atau /skip.",
        reply_markup=kb)
    return BUKTI


# ---------- step 5: bukti ----------

async def photo_bukti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        return BUKTI
    wait = await update.message.reply_text("⏳ Upload bukti ke Drive...")
    try:
        c = context.user_data["cls"]
        tanggal = _tanggal_kelas(context, c)
        rec_tmp = _build_base(context, c, tanggal)
        photo = update.message.photo[-1]
        tg_file = await photo.get_file()
        data = bytes(await tg_file.download_as_bytearray())
        ext = "jpg"
        if (tg_file.file_path or "").lower().endswith(".png"):
            ext = "png"
        fname = rec_tmp.bukti_filename(ext)
        link = await _sheets(context).upload_bukti(data, fname, f"image/{ext}", rec_tmp.facilitator)
        context.user_data["bukti"] = link
        context.user_data["bukti_name"] = fname
        try: await wait.delete()
        except Exception: pass
        await update.message.reply_text(f"✅ Bukti terupload: {rec_tmp.bukti_filename(ext)}")
    except sheets.SheetsError as exc:
        try: await wait.delete()
        except Exception: pass
        await update.message.reply_text(f"⚠️ Upload gagal: {exc}\nPaste link manual atau /skip.")
        return BUKTI
    except Exception as exc:
        try: await wait.delete()
        except Exception: pass
        await update.message.reply_text(f"⚠️ Upload gagal: {exc}\nPaste link manual atau /skip.")
        return BUKTI
    if context.user_data.get("fix_row"):
        return await _confirm_fix(update.message, context)
    return await _confirm(update.message, context)


async def link_bukti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.lower().startswith("http"):
        await update.message.reply_text("Link harus diawali http. Paste link Drive atau /skip.")
        return BUKTI
    context.user_data["bukti"] = text
    context.user_data["bukti_name"] = ""
    if context.user_data.get("fix_row"):
        return await _confirm_fix(update.message, context)
    return await _confirm(update.message, context)


async def skip_bukti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["bukti"] = ""
    context.user_data["bukti_name"] = ""
    if context.user_data.get("fix_row"):
        return await _confirm_fix(update.message, context)
    return await _confirm(update.message, context)


# ---------- helpers ----------

def _tanggal_kelas(context, c) -> str:
    """Tanggal Kehadiran format panjang '7 September 2026'."""
    if c.category == "Backup" and c.backup_hari_tanggal:
        return c.backup_hari_tanggal.strip()
    return sheets.tanggal_panjang(sheets.next_date_for_day(c.day))


def _build_base(context, c, tanggal) -> sheets.RekapRecord:
    meeting = context.user_data.get("meeting", "")
    return sheets.RekapRecord(
        facilitator=context.user_data.get("facilitator") or "",
        bukti_name=context.user_data.get("bukti_name", ""),
        tanggal=tanggal,
        lecturer=c.lecturer,
        jam=c.time_range,
        kode=c.code,
        subject=c.subject,
        sks=c.sks,
        pertemuan=meeting,
        tipe=context.user_data.get("tipe", ""),
        sesi=context.user_data.get("sesi", "Kelas Biasa"),
        peran=context.user_data.get("peran") or ("Backup Fasil" if c.category == "Backup" else "Fasilitator Kelas"),
        bukti=context.user_data.get("bukti", ""),
    )


# ---------- step 6: confirm ----------

async def _confirm(message: Message, context: ContextTypes.DEFAULT_TYPE) -> int:
    c = context.user_data["cls"]
    tanggal = _tanggal_kelas(context, c)
    meeting = context.user_data.get("meeting", "")
    wait = await message.reply_text("⏳ Hitung kehadiran (Absen + Feedback)...")
    counts, fb = None, None
    try:
        counts = await _sheets(context).absen_counts(c.code, _first_num(meeting))
    except Exception:
        counts = None
    try:
        fb = await _sheets(context).feedback_counts(
            c.code, meeting, c.lecturer, (counts or {}).get("prodi", ""))
    except Exception:
        fb = None
    try: await wait.delete()
    except Exception: pass
    if counts and counts["total"]:
        o, p, r = str(counts["total"]), str(counts["hadir"]), str(counts["tidak"])
        if fb is not None:
            qf = str(fb["q"])
            s = str(max(counts["total"] - fb["q"], 0))
            oz_note = f" (Absen: total {o}, hadir {p} | Feedback bersih: {qf})"
        else:
            qf, s = "", ""
            oz_note = f" (Absen: total {o}, hadir {p} | Feedback gagal dibaca)"
    else:
        o = p = qf = r = s = ""
        oz_note = " (Absen kosong/belum ada — O-W dikosongkan)"
    context.user_data["counts"] = (o, p, qf, r, s)
    rec = _build_base(context, c, tanggal)
    rec.total, rec.hadir, rec.feedback, rec.tidak, rec.belum = o, p, qf, r, s
    rows = [
        ("Tanggal", rec.tanggal), ("Dosen", rec.lecturer),
        ("Jam", rec.jam), ("Kelas", rec.kode),
        ("Matkul", rec.subject), ("SKS", rec.sks),
        ("Pertemuan", rec.pertemuan), ("Tipe", rec.tipe),
        ("Sesi", rec.sesi), ("Peran", rec.peran),
        ("Bukti", rec.bukti or "—"),
        ("Total/Hadir/Feedback", f"{o or '-'}/{p or '-'}/{qf or '-'}"),
    ]
    lines = "\n".join(f"• <b>{k}:</b> {v}" for k, v in rows)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Submit", callback_data="rkx:ok"),
         InlineKeyboardButton("❌ Batal", callback_data="rkx:no")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="rkb:bukti")],
    ])
    await message.reply_text(f"📋 <b>Konfirmasi Rekap:</b>\n{lines}{oz_note}\n\nSubmit?", parse_mode=ParseMode.HTML, reply_markup=kb)
    return CONFIRM


async def confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    # Per-chat lock (see handlers/log.py confirm_cb).
    async with _sheets(context).for_chat(update.effective_chat.id):
        if q.data.endswith(":no"):
            await q.message.reply_text("Dibatalkan. Kirim /rekap untuk coba lagi.")
            return ConversationHandler.END
        c = context.user_data["cls"]
        tanggal = _tanggal_kelas(context, c)
        o, p, qf, r, s = context.user_data.get("counts", ("", "", "", "", ""))
        rec = _build_base(context, c, tanggal)
        rec.total, rec.hadir, rec.feedback, rec.tidak, rec.belum = o, p, qf, r, s
        tab = context.user_data.get("rekap_tab", "")
        fix_row = context.user_data.get("fix_row")
        busy = await q.message.reply_text("⏳ Menyimpan ke Rekap...")
        try:
            if fix_row:
                cells = {"O": o, "P": p, "Q": qf, "R": r, "S": s}
                if rec.bukti.startswith("http"):
                    if rec.bukti_name:
                        url = rec.bukti.replace('"', "")
                        cells["L"] = f'=HYPERLINK("{url}","{rec.bukti_name.replace(chr(34), "")}")'
                    else:
                        cells["L"] = rec.bukti
                # Only fill empties for B-K to avoid clobbering manual edits
                try:
                    cur = await _sheets(context).rekap_row_values(tab, fix_row)
                    cols = ["B", "C", "D", "E", "F", "G", "H", "I", "J", "K"]
                    vals = [rec.tanggal, rec.lecturer, rec.jam, rec.kode, rec.subject,
                            rec.sks, rec.pertemuan, rec.tipe, rec.sesi, rec.peran]
                    for col, v in zip(cols, vals):
                        idx = ord(col) - 65
                        if v and (len(cur) <= idx or not cur[idx].strip()):
                            cells[col] = v
                except Exception:
                    pass
                cells = {k: v for k, v in cells.items() if v}
                await _sheets(context).update_rekap_cells(tab, fix_row, cells)
            else:
                await _sheets(context).append_rekap_record(tab, rec)
        except sheets.SheetsError as exc:
            try: await busy.delete()
            except Exception: pass
            await q.message.reply_text(f"⚠️ Gagal simpan: {exc}\nTekan ✅ untuk retry.")
            return CONFIRM
        try: await busy.delete()
        except Exception: pass
        log.info("Saved rekap: %s %s", rec.kode, rec.pertemuan)
        usage.log(update.effective_chat.id, rec.facilitator, "rekap", rec.kode)
        done_msg = f" (update baris {fix_row})" if fix_row else ""
        await q.message.reply_text(f"✅ Rekap tercatat di tab {tab}! {rec.kode} — pertemuan {rec.pertemuan}{done_msg}.\nKirim /rekap untuk entry berikutnya.")
        context.user_data.clear()
        return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Dibatalkan.")
    return ConversationHandler.END


async def timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text("⏱ Form timeout (1 jam). Kirim /rekap untuk mulai lagi.")
    return ConversationHandler.END


# ---------- back ----------

async def back_to_rk_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    kb_rows = context.user_data.get("class_kb")
    if not kb_rows:
        await q.message.reply_text("Kembali ke awal — kirim /rekap lagi.")
        return ConversationHandler.END
    await q.message.reply_text("1️⃣ Pilih kelas:", reply_markup=InlineKeyboardMarkup(kb_rows))
    return CLASS


async def back_rk_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Dibatalkan.")
    return ConversationHandler.END


def register(app: Application, cfg: Config) -> None:
    conv = ConversationHandler(
        entry_points=[CommandHandler("rekap", cmd_rekap),
                      CallbackQueryHandler(cmd_rekap, pattern=r"^go:rekap$")],
        states={
            CLASS: [CallbackQueryHandler(pick_class, pattern=r"^rkc:\d+$"), CallbackQueryHandler(back_rk_cancel, pattern=r"^rkb:cancel$"), CallbackQueryHandler(fix_choice, pattern=r"^rkfix:(yes|new)$"), CallbackQueryHandler(_begin_meeting_cb, pattern=r"^rknew:go$")],
            MEETING: [CallbackQueryHandler(pick_meeting, pattern=r"^rkm:"), CallbackQueryHandler(back_to_rk_class, pattern=r"^rkb:class$"), MessageHandler(_SKIP_FILTER, enter_meeting)],
            TIPE: [CallbackQueryHandler(pick_tipe, pattern=r"^rkt:[os]$"), CallbackQueryHandler(back_to_rk_meeting, pattern=r"^rkb:meeting$")],
            SESI: [CallbackQueryHandler(pick_sesi, pattern=r"^rks:\d+$"), CallbackQueryHandler(back_to_rk_tipe, pattern=r"^rkb:tipe$"), MessageHandler(_SKIP_FILTER, enter_sesi)],
            PERAN: [CallbackQueryHandler(pick_peran, pattern=r"^rkp:\d+$")],
            BUKTI: [CallbackQueryHandler(back_to_rk_sesi, pattern=r"^rkb:sesi$"), CallbackQueryHandler(back_to_rk_peran, pattern=r"^rkb:peran$"), CommandHandler("skip", skip_bukti), MessageHandler(filters.PHOTO, photo_bukti), MessageHandler(_SKIP_FILTER, link_bukti)],
            CONFIRM: [CallbackQueryHandler(confirm_cb, pattern=r"^rkx:(ok|no)$"), CallbackQueryHandler(back_to_rk_bukti, pattern=r"^rkb:bukti$")],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, timeout)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=_TIMEOUT,
        name="rekap_conv",
    )
    app.add_handler(conv)


async def back_to_rk_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    c = context.user_data.get("cls")
    last, nxt, nxt2 = context.user_data.get("suggest", ("", "1", "1 dan 2"))
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Pakai {nxt}", callback_data=f"rkm:{nxt}"),
         InlineKeyboardButton(f"🔄 Jadi {nxt2}", callback_data=f"rkm:{nxt2}")],
        [InlineKeyboardButton("✏️ Ketik manual", callback_data="rkm:manual")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="rkb:class")],
    ])
    if last and c:
        await q.message.reply_text(f"2️⃣ Terakhir {c.code} pertemuan {last}. Mau isi {nxt}?", reply_markup=kb)
    else:
        await q.message.reply_text(f"2️⃣ Pertemuan ke-berapa? (suggest {nxt})", reply_markup=kb)
    return MEETING


async def back_to_rk_tipe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Online", callback_data="rkt:o"),
         InlineKeyboardButton("🏫 On-site", callback_data="rkt:s")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="rkb:meeting")],
    ])
    await q.message.reply_text("3️⃣ Tipe kelas:", reply_markup=kb)
    return TIPE


async def back_to_rk_sesi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Kelas Biasa", callback_data="rks:biasa")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="rkb:tipe")],
    ])
    await q.message.reply_text("4️⃣ Sesi kelas? (atau ketik manual)", reply_markup=kb)
    return SESI


async def back_to_rk_bukti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali", callback_data="rkb:peran")]])
    await q.message.reply_text("6️⃣ Bukti kehadiran? Kirim **foto** screenshot, paste **link Drive**, atau /skip.", reply_markup=kb)
    return BUKTI


async def back_to_rk_peran(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    c = context.user_data.get("cls")
    suggest = "Backup Fasil" if (c and c.category == "Backup") else "Fasilitator Kelas"
    await q.message.reply_text(f"5️⃣ Peran? (saran: {suggest})", reply_markup=_peran_kb(suggest))
    return PERAN
