"""/log — ConversationHandler: 6-step Zoom Record entry form."""
from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime, timedelta

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

import pola
import sheets
import usage
import users
from config import Config

log = logging.getLogger(__name__)

CLASS, DATE, MEETING, SKEMA, ZOOM, NOTE, CONFIRM = range(7)
_TIMEOUT = 60 * 60  # 1 hour

_SKIP_FILTER = filters.TEXT & ~filters.COMMAND


def _sheets(context: ContextTypes.DEFAULT_TYPE) -> sheets.SheetsClient:
    return context.bot_data["sheets"]


async def _fetch_classes(context: ContextTypes.DEFAULT_TYPE, facilitator_name: str) -> list[sheets.ClassEntry]:
    classes = await _sheets(context).get_classes(facilitator_name)
    context.user_data["classes"] = classes
    return classes


# ---------- picker grup bertingkat (kombinasi 1+2) ----------

def _dmy_to_date(s: str) -> date:
    d, m, y = s.split("/")
    return datetime(int(y), int(m), int(d)).date()


def _week_span(today=None):
    """(Senin, Minggu) minggu WIB ini. `today` hanya untuk test."""
    t = today or datetime.now(sheets.WIB)
    mon = t - timedelta(days=t.weekday())
    return mon.date(), (mon + timedelta(days=6)).date()


def _class_done_key(c: sheets.ClassEntry) -> tuple[str, str]:
    """(kode.casefold(), dd/mm/yyyy) — konvensi SAMA dgn done_by_date sheets."""
    if c.category in ("Backup", "Make-up"):
        if c.backup_hari_tanggal:
            return (c.code.casefold(), _parse_backup_date(c.backup_hari_tanggal))
        return (c.code.casefold(), "")
    return (c.code.casefold(), sheets.last_date_for_day(c.day))


def _class_week_date(c: sheets.ClassEntry, mon, sun):
    """Tanggal kelas yg jatuh di minggu ini (date) atau None.
    Personal: last hari dalam minggu; jika last < Senin tapi next masih
    di minggu ini -> jadwal mendatang (next). Backup/Make-up: tanggal eksplisit."""
    if c.category in ("Backup", "Make-up") and c.backup_hari_tanggal:
        try:
            d = _dmy_to_date(_parse_backup_date(c.backup_hari_tanggal))
        except Exception:
            return None
        return d if mon <= d <= sun else None
    try:
        last = _dmy_to_date(sheets.last_date_for_day(c.day))
        nxt = _dmy_to_date(sheets.next_date_for_day(c.day))
    except Exception:
        return None
    if mon <= last <= sun:
        return last
    if last < mon and mon <= nxt <= sun:
        return nxt
    return None


def _class_sort_date(c: sheets.ClassEntry) -> date:
    """Tanggal rujukan utk urut lama->baru dalam grup."""
    if c.category in ("Backup", "Make-up") and c.backup_hari_tanggal:
        try:
            return _dmy_to_date(_parse_backup_date(c.backup_hari_tanggal))
        except Exception:
            return date.max
    try:
        return _dmy_to_date(sheets.last_date_for_day(c.day))
    except Exception:
        return date.max


def _group_picker_classes(classes: list[sheets.ClassEntry], done_by_date: set, today=None):
    """Klasifikasi 3 grup: MINGGU INI / SEBELUMNYA (belum di-log) / SUDAH DI-LOG.
    Masing-masing urut tanggal lama->baru. `today` hanya untuk test (WIB)."""
    mon, sun = _week_span(today)
    week: list[tuple[date, sheets.ClassEntry]] = []
    arrears: list[sheets.ClassEntry] = []
    done: list[sheets.ClassEntry] = []
    for c in classes:
        if _class_done_key(c) in done_by_date:
            done.append(c)
            continue
        wd = _class_week_date(c, mon, sun)
        if wd is not None:
            week.append((wd, c))
        else:
            arrears.append(c)
    week.sort(key=lambda t: t[0])
    arrears.sort(key=_class_sort_date)
    done.sort(key=_class_sort_date)
    return [c for _, c in week], arrears, done


def _class_label(c: sheets.ClassEntry, done_by_date: set) -> str:
    """Label lama: {✅}🔄/🧪 kode — matkul (tgl)."""
    prefix = "✅ " if _class_done_key(c) in done_by_date else ""
    if c.category == "Backup":
        return f"{prefix}🔄 {c.code} — {c.subject} ({c.backup_hari_tanggal})"
    if c.category == "Make-up":
        return f"{prefix}🧪 {c.code} — {c.subject} ({c.backup_hari_tanggal})"
    return f"{prefix}{c.code} — {c.subject} ({c.day} {c.time_range})"


def _build_class_view(classes: list[sheets.ClassEntry], done_by_date: set, show_done: bool = False, today=None):
    """(text, kb_rows) picker grup bertingkat. Header grup = baris teks non-interaktif
    (pola absen). Grup SUDAH DI-LOG tersembunyi default; toggle zoom_show_done menampilkannya."""
    week, arrears, done = _group_picker_classes(classes, done_by_date, today)
    groups = [("📅 MINGGU INI", week), ("⏳ SEBELUMNYA (belum di-log)", arrears)]
    if show_done:
        groups.append(("✅ SUDAH DI-LOG", done))
    text_parts = []
    if week or arrears:
        text_parts.append("1️⃣ Pilih kelas: (⭐ kelas sendiri · 🔄 backup · 🧪 make-up · ✅ sudah)")
    else:
        text_parts.append("Tidak ada kelas minggu ini")
    for header, items in groups:
        if items:
            text_parts.append(f"— {header} —")
    text = "\n".join(text_parts)
    kb_rows = []
    idx = 0  # indeks tombol = posisi di classes terurut (week+arrears+done)
    for _, items in groups:
        for c in items:
            kb_rows.append([InlineKeyboardButton(_class_label(c, done_by_date), callback_data=f"c:{idx}")])
            idx += 1
    if done:
        if show_done:
            kb_rows.append([InlineKeyboardButton("🙈 Sembunyikan sudah di-log", callback_data="vd:0")])
        else:
            kb_rows.append([InlineKeyboardButton(f"👁 ({len(done)}) sudah di-log", callback_data="vd:1")])
    kb_rows.append([InlineKeyboardButton("❌ Batal", callback_data="back:cancel")])
    return text, kb_rows


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
        personal, backup, makeup = await _sheets(context).get_all_loggable_classes(facilitator)
    except sheets.SheetsError as exc:
        try: await loading.delete()
        except Exception: pass
        await update.effective_message.reply_text(f"⚠️ {exc}")
        return ConversationHandler.END
    try: await loading.delete()
    except Exception: pass
    classes = personal + backup + makeup
    if not classes:
        await update.effective_message.reply_text(f"Tidak ada kelas untuk {facilitator} (jadwal + backup kosong).")
        return ConversationHandler.END
    context.user_data["classes"] = classes
    # Check which classes have been filled for their last scheduled date + jam
    try:
        done_by_date = await _sheets(context).get_done_by_date(facilitator)
    except Exception: done_by_date = set()
    week, arrears, done = _group_picker_classes(classes, done_by_date)
    ordered = week + arrears + done
    context.user_data["classes"] = ordered
    context.user_data["done_by_date"] = done_by_date
    show_done = bool(context.user_data.get("zoom_show_done"))
    text, kb_rows = _build_class_view(ordered, done_by_date, show_done)
    context.user_data["class_kb"] = kb_rows
    context.user_data["class_text"] = text
    await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb_rows))
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
    await q.message.reply_text(context.user_data.get("class_text") or "1️⃣ Pilih kelas:",
                               reply_markup=InlineKeyboardMarkup(kb_rows))
    return CLASS


async def toggle_done_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    classes = context.user_data.get("classes") or []
    if not classes:
        await q.message.reply_text("Kembali ke awal — kirim /zoom lagi.")
        return ConversationHandler.END
    context.user_data["zoom_show_done"] = q.data == "vd:1"
    done_by_date = context.user_data.get("done_by_date") or set()
    text, kb_rows = _build_class_view(classes, done_by_date, context.user_data.get("zoom_show_done"))
    context.user_data["class_kb"] = kb_rows
    context.user_data["class_text"] = text
    try:
        await q.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb_rows))
    except Exception:
        await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb_rows))
    return CLASS


async def back_to_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    c = context.user_data.get("cls")
    last, def_nxt, def_nxt2 = context.user_data.get("suggest", ("", "1", "1 dan 2"))
    # suggest bisa kedaluwarsa — pakai meeting aktual (hasil pick/ketik) utk display.
    cur = str(context.user_data.get("meeting") or "").strip()
    nums = [int(n) for n in re.findall(r"\d+", cur)]
    if nums:
        nxt = cur
        nxt2 = f"{max(nums)} dan {max(nums) + 1}"
        context.user_data["suggest"] = (last, nxt, nxt2)
    else:
        nxt, nxt2 = def_nxt, def_nxt2
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


def _date_kb(c: sheets.ClassEntry) -> InlineKeyboardMarkup:
    last = sheets.last_date_for_day(c.day)
    nxt = sheets.next_date_for_day(c.day)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📅 {sheets.tanggal_panjang(nxt)} · jadwal berikutnya", callback_data="d:next")],
        [InlineKeyboardButton(f"📅 {sheets.tanggal_panjang(last)} · pertemuan terakhir", callback_data="d:last")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="back:class")],
    ])


async def _meeting_step(q, context: ContextTypes.DEFAULT_TYPE, c: sheets.ClassEntry) -> int:
    """Step 2 render (lanjutan pick_class / pick_date). Default meeting = gap-min."""
    # Auto-suggest next meeting (lanjut logis gap-min, bukan max+1)
    wait = await q.message.reply_text("⏳ Cari pertemuan terakhir...")
    try:
        last, nxt, nxt2 = await _sheets(context).get_next_meeting(c.code)
    except Exception:
        last, nxt, nxt2 = "", "1", "1 dan 2"
    try: await wait.delete()
    except Exception: pass
    context.user_data["suggest"] = (last, nxt, nxt2)
    context.user_data["meeting"] = nxt  # default pintar — bisa diubah di picker
    warn = await _meeting_warnings(context, context.user_data.get("facilitator", ""), c, nxt)
    text = f"2️⃣ Pertemuan: <b>{html.escape(nxt)}</b>"
    if last:
        text += f" <i>(terakhir {html.escape(last)})</i>"
    if warn:
        text += "\n" + warn
    await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=_meeting_kb(nxt, nxt2))
    return MEETING


async def pick_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split(":")[1])
    classes: list[sheets.ClassEntry] = context.user_data.get("classes") or []
    if not 0 <= idx < len(classes):
        await q.message.reply_text("Pilihan kedaluwarsa — kirim /log lagi.")
        return ConversationHandler.END
    c = classes[idx]
    context.user_data["cls"] = c
    context.user_data.pop("lecture_date", None)  # override tanggal — reset tiap pick kelas
    await q.message.edit_text(f"Kelas: <b>{html.escape(c.code)}</b> — {html.escape(c.subject)}\n\n", parse_mode=ParseMode.HTML)
    # Kelas personal: tawarkan tanggal (pertemuan terakhir vs jadwal berikutnya),
    # sebelum lanjut ke pertemuan. Default = jadwal berikutnya (perilaku lama).
    # Backup/make-up tanggal eksplisit -> langsung ke step pertemuan.
    if c.category not in ("Backup", "Make-up"):
        last = sheets.last_date_for_day(c.day)
        nxt = sheets.next_date_for_day(c.day)
        if last != nxt:
            await q.message.reply_text("📅 Tanggal kelasnya? — pilih salah satu (default jadwal berikutnya)",
                                       reply_markup=_date_kb(c))
            return DATE
    return await _meeting_step(q, context, c)


async def pick_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    c = context.user_data.get("cls")
    if not c:
        await q.message.reply_text("Pilihan kedaluwarsa — kirim /zoom lagi.")
        return ConversationHandler.END
    context.user_data["lecture_date"] = (
        sheets.last_date_for_day(c.day) if q.data == "d:last" else sheets.next_date_for_day(c.day)
    )
    return await _meeting_step(q, context, c)


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
    warn = ""
    c = context.user_data.get("cls")
    if c:
        warn = await _meeting_warnings(context, context.user_data.get("facilitator", ""), c, val)
    body = warn + "\n\n3️⃣ Skema kelas:" if warn else "3️⃣ Skema kelas:"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Online", callback_data="s:o"), InlineKeyboardButton("🏫 Offline", callback_data="s:f")],
        [InlineKeyboardButton("◀️ Kembali", callback_data="back:meeting")],
    ])
    await q.message.reply_text(body, reply_markup=kb)
    return SKEMA

async def enter_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    nums = re.findall(r"\d+", text)
    if not nums or not all(1 <= int(n) <= 99 for n in nums):
        await update.effective_message.reply_text("Masukkan pertemuan 1–99 (bisa 1, 2 atau '1 dan 2').")
        return MEETING
    context.user_data["meeting"] = text
    warn = ""
    c = context.user_data.get("cls")
    if c:
        warn = await _meeting_warnings(context, context.user_data.get("facilitator", ""), c, text)
    body = warn + "\n\n3️⃣ Skema kelas:" if warn else "3️⃣ Skema kelas:"
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🌐 Online", callback_data="s:o"),
          InlineKeyboardButton("🏫 Offline", callback_data="s:f")],
         [InlineKeyboardButton("◀️ Kembali", callback_data="back:meeting")]]
    )
    await update.effective_message.reply_text(body, reply_markup=kb)
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
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", s)
    if not m:
        return sheets.today_str_wib()
    d, mon, y = m.group(1), m.group(2).lower(), m.group(3)
    months = {"januari": "01", "februari": "02", "maret": "03", "april": "04", "mei": "05", "juni": "06", "juli": "07", "agustus": "08", "september": "09", "oktober": "10", "november": "11", "desember": "12"}
    mm = months.get(mon, "01")
    return f"{int(d):02d}/{mm}/{y}"


def _class_date(context: ContextTypes.DEFAULT_TYPE, c: sheets.ClassEntry) -> str:
    """Tanggal kelas (dd/mm/yyyy) — key dupe gate + link ke pertemuan.
    Backup/make-up pakai Hari/Tanggal eksplisit; personal pakai pilihan user
    (default jadwal berikutnya)."""
    if c.category in ("Backup", "Make-up") and c.backup_hari_tanggal:
        return _parse_backup_date(c.backup_hari_tanggal)
    return context.user_data.get("lecture_date") or sheets.next_date_for_day(c.day)


def _pola_warning(facilitator: str, kode: str, meeting: str) -> str:
    """🟡 warning pola (data/pola.json) — bukan blokir. Tanpa config = kosong."""
    pmap = pola.pola_for(kode)
    if not pmap:
        return ""
    nums = sorted({int(n) for n in re.findall(r"\d+", meeting or "")})
    hits = [pmap[str(n)] for n in nums if str(n) in pmap]
    if not hits or not facilitator:
        return ""
    fn = re.sub(r"\s+", " ", facilitator.strip()).casefold()
    bad = [h for h in hits if not (h.casefold() in fn or fn in h.casefold())]
    if not bad:
        return ""
    return (f"🟡 <b>Pola {html.escape(kode)}</b>: pertemuan {html.escape(meeting)} "
            f"biasanya dipegang <b>{html.escape(', '.join(sorted(set(bad))))}</b>. "
            f"Ingatkan saja, bukan blokir — Yakin tetap lanjut?")


def _conflict_report_text(code: str, tanggal: str, meeting: str, found: list) -> str:
    """Teks dupe gate: siapa perekam yang sudah rekam key sama."""
    lines = []
    for f in found:
        who = html.escape(f.get("fasil") or "(tanpa nama)")
        tipe = html.escape(f.get("tipe") or "")
        lines.append(f"• <b>{who}</b> — pertemuan {html.escape(f.get('pertemuan', '') or '')} ({tipe})")
    return (
        f"⚠️ Sudah ada rekaman utk <b>{html.escape(code)}</b> "
        f"({html.escape(tanggal)}) pertemuan <b>{html.escape(meeting)}</b>:\n"
        + "\n".join(lines)
        + "\n\nPilih: pakai nomor lain, atau koreksi (tetap simpan)."
    )


async def _meeting_warnings(context: ContextTypes.DEFAULT_TYPE, facilitator: str,
                            c: sheets.ClassEntry, meeting: str) -> str:
    """Gabungan warning pertemuan: pola (config) + konflik dupe (kode+tanggal+ptm).
    Fail-open: sheets error diabaikan — picker tetap jalan."""
    warnings = []
    pola_warn = _pola_warning(facilitator, c.code, meeting)
    if pola_warn:
        warnings.append(pola_warn)
    try:
        found = await _sheets(context).find_conflicts(c.code, _class_date(context, c), meeting)
    except Exception:
        found = []
    if found:
        warnings.append(_conflict_report_text(c.code, _class_date(context, c), meeting, found))
    return "\n".join(warnings)

def _build_record(context: ContextTypes.DEFAULT_TYPE) -> sheets.LogRecord:
    c: sheets.ClassEntry = context.user_data["cls"]
    # Zoom: user input (step 4) OR auto from Jadwal Fasil
    zoom = context.user_data.get("zoom", "") or c.zoom_label
    # Tanggal kelas: backup/make-up eksplisit, personal pilihan user (default berikutnya).
    lecture_date = _class_date(context, c)
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
    lines = "\n".join(f"• <b>{html.escape(k)}:</b> {html.escape(v)}" for k, v in rows)
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
        # Dupe gate (kode, tanggal, pertemuan): jangan bikin dobel diam-diam.
        # Tampilkan siapa perekam + tawarkan nomor lain / koreksi.
        try:
            found = await _sheets(context).find_conflicts(rec.code, rec.lecture_date, rec.meeting)
        except Exception:
            found = []
        if found and not context.user_data.get("force_conflict"):
            context.user_data["_conflicts"] = found
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Pakai nomor lain", callback_data="back:meeting"),
                 InlineKeyboardButton("✅ Tetap simpan (koreksi)", callback_data="x:force")],
                [InlineKeyboardButton("❌ Batal", callback_data="x:no")],
            ])
            await q.message.reply_text(
                _conflict_report_text(rec.code, rec.lecture_date, rec.meeting, found),
                parse_mode=ParseMode.HTML, reply_markup=kb)
            return CONFIRM
        retries = context.user_data.get("_retries", 0)
        busy = await q.message.reply_text("⏳ Menyimpan ke Zoom Record...")
        try:
            await _sheets(context).append_record(rec)
        except sheets.SheetsError as exc:
            retries += 1
            context.user_data["_retries"] = retries
            try: await busy.delete()
            except Exception: pass
            if retries >= 3:
                await q.message.reply_text(f"⚠️ Gagal 3x: {exc}\nData tidak tersimpan. Kirim /log untuk mulai ulang.")
                context.user_data.clear()
                return ConversationHandler.END
            retry_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Retry", callback_data="x:ok"),
                 InlineKeyboardButton("❌ Batal", callback_data="x:no")],
            ])
            await q.message.reply_text(f"⚠️ Gagal simpan ({retries}/3): {exc}\nData belum tersimpan.", reply_markup=retry_kb)
            return CONFIRM
        try: await busy.delete()
        except Exception: pass
        # Force lewat dupe gate hanya valid utk simpan ini — clear setelah sukses,
        # bukan sebelum retry, agar Retry tak minta force ulang.
        context.user_data.pop("force_conflict", None)
        log.info("Saved record: %s mtg %s scheme %s", rec.code, rec.meeting, rec.scheme)
        # Fail-open: usage tracking tak pernah mematahkan alur utama.
        usage.log(update.effective_chat.id, rec.facilitator, "zoom", rec.code,
                  tipe=rec.tipe_kelas, meeting=rec.meeting)
        await q.message.reply_text(f"✅ Tercatat di sheet! {rec.code} — pertemuan {rec.meeting} ({rec.scheme}).\nKirim /log untuk entry berikutnya.")
        context.user_data.clear()
        return ConversationHandler.END


async def confirm_force(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Koreksi sadar dari dupe gate: lanjut simpan walau key sudah direkam."""
    q = update.callback_query
    await q.answer()
    context.user_data["force_conflict"] = True
    return await confirm_cb(update, context)


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
            CLASS: [CallbackQueryHandler(pick_class, pattern=r"^c:\d+$"),
                    CallbackQueryHandler(toggle_done_view, pattern=r"^vd:[01]$"),
                    CallbackQueryHandler(back_cancel, pattern=r"^back:cancel$")],
            DATE: [CallbackQueryHandler(pick_date, pattern=r"^d:(next|last)$"),
                   CallbackQueryHandler(back_to_class, pattern=r"^back:class$")],
            MEETING: [CallbackQueryHandler(pick_meeting, pattern=r"^m:"), CallbackQueryHandler(back_to_class, pattern=r"^back:class$"), MessageHandler(_SKIP_FILTER, enter_meeting)],
            SKEMA: [CallbackQueryHandler(pick_scheme, pattern=r"^s:[of]$"), CallbackQueryHandler(back_to_meeting, pattern=r"^back:meeting$")],
            ZOOM: [MessageHandler(_SKIP_FILTER, enter_zoom)],
            NOTE: [
                CallbackQueryHandler(skip_note_cb, pattern=r"^n:skip$"),
                CallbackQueryHandler(back_to_scheme, pattern=r"^back:scheme$"),
                CommandHandler("skip", skip_note),
                MessageHandler(_SKIP_FILTER, enter_note),
            ],
            CONFIRM: [CallbackQueryHandler(confirm_cb, pattern=r"^x:(ok|no)$"), CallbackQueryHandler(confirm_force, pattern=r"^x:force$"), CallbackQueryHandler(back_to_note, pattern=r"^back:note$"), CallbackQueryHandler(back_to_meeting, pattern=r"^back:meeting$")],
            # v22: on_timeout param removed — next update after timeout lands in TIMEOUT state
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, timeout)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", cancel), CommandHandler(["log", "zoom", "zoom_record"], cmd_log)],
        conversation_timeout=_TIMEOUT,
        name="log_conv",
        allow_reentry=True,
    )
    app.add_handler(conv)
