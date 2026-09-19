"""/sinkron <kode> <pertemuan> — naikkan SF/OF -> S/O di absen untuk mahasiswa
yang SUDAH isi feedback form (fakta feedback, bukan fabrikasi). Manual twin dari
sinkron otomatis setelah /rekap sukses.

Alur: command -> preview max 10 perubahan + tombol ✅ Eksekusi / ❌ Batal.
Konversi: sama persis dengan rekap (feedback_nims + convert_status).
"""
from __future__ import annotations

import html
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

CONFIRM = 0
_TIMEOUT = 10 * 60


def _sheets(context: ContextTypes.DEFAULT_TYPE) -> sheets.SheetsClient:
    return context.bot_data["sheets"]


async def cmd_sinkron(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not users.get(update.effective_chat.id):
        await update.effective_message.reply_text(users.UNREGISTERED_MSG)
        return ConversationHandler.END
    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Cara pakai: <code>/sinkron &lt;kode&gt; &lt;pertemuan&gt;</code>\n"
            "Contoh: <code>/sinkron 3Ilkom 3</code> atau <code>/sinkron 3Ilkom 3 4</code> "
            "(dua pertemuan sekaligus).",
            parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    kode = args[0].strip()
    nums = sorted({int(n) for n in re.findall(r"\d+", " ".join(args[1:])) if 1 <= int(n) <= 16})
    if not nums:
        await update.effective_message.reply_text("Pertemuan harus angka 1–16.")
        return ConversationHandler.END
    busy = await update.effective_message.reply_text("⏳ Hitung calon perubahan (absen SF/OF + feedback)...")
    try:
        try:
            nims = await _sheets(context).feedback_nims(kode, nums, "")
        except sheets.SheetsError as exc:
            await update.effective_message.reply_text(f"⚠️ {exc}")
            return ConversationHandler.END
        plan: list[dict] = []
        seen: set[tuple] = set()
        for pm in nums:
            changes = await _sheets(context).plan_convert_status(kode, pm, nims)
            for ch in changes:
                key = (ch["tab"], ch["row"])
                if key in seen:
                    continue
                seen.add(key)
                plan.append({**ch, "pertemuan": pm})
    finally:
        try: await busy.delete()
        except Exception: pass
    if not plan:
        await update.effective_message.reply_text(
            "✅ Tidak ada yang perlu disinkron: 0 mahasiswa berstatus SF/OF yang sudah isi "
            f"feedback (untuk {kode}, pertemuan {'+'.join(map(str, nums))}).")
        return ConversationHandler.END
    context.user_data["sync_kode"] = kode
    context.user_data["sync_nums"] = nums
    total_sf = sum(1 for c in plan if c["dari"] == "SF")
    total_of = sum(1 for c in plan if c["dari"] == "OF")
    lines = [f"📋 <b>Preview /sinkron {html.escape(kode)} — pertemuan "
             f"{'+'.join(map(str, nums))}:</b>"]
    for ch in plan[:10]:
        lines.append(
            f"• p.{ch['pertemuan']} <code>{html.escape(ch['nim'])}</code> "
            f"{html.escape(ch['nama'])}: {ch['dari']}→{ch['ke']} ({html.escape(ch['tab'])})")
    if len(plan) > 10:
        lines.append(f"… dan {len(plan) - 10} perubahan lainnya")
    lines.append(f"Total: <b>{total_sf} SF→S</b>, <b>{total_of} OF→O</b>.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Eksekusi", callback_data="skn:ok"),
         InlineKeyboardButton("❌ Batal", callback_data="skn:no")],
    ])
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb)
    return CONFIRM


async def confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data.endswith(":no"):
        await q.message.reply_text("Dibatalkan.")
        return ConversationHandler.END
    kode = context.user_data.get("sync_kode", "")
    nums = context.user_data.get("sync_nums") or []
    if not kode or not nums:
        await q.message.reply_text("Sesi kedaluwarsa — jalankan /sinkron lagi.")
        return ConversationHandler.END
    # Per-chat lock (pola sama handlers/log.py confirm_cb).
    async with _sheets(context).for_chat(update.effective_chat.id):
        busy = await q.message.reply_text("⏳ Mengeksekusi sinkron...")
        try:
            nims = await _sheets(context).feedback_nims(kode, nums, "")
            n_sf = n_of = 0
            for pm in nums:
                changes = await _sheets(context).convert_status(kode, pm, nims)
                for ch in changes:
                    if ch["dari"] == "SF":
                        n_sf += 1
                    else:
                        n_of += 1
        except sheets.SheetsError as exc:
            try: await busy.delete()
            except Exception: pass
            await q.message.reply_text(f"⚠️ Gagal sinkron: {exc}")
            context.user_data.clear()
            return ConversationHandler.END
        except Exception as exc:  # noqa: BLE001
            log.exception("sinkron execute crash %s %s", kode, nums)
            try: await busy.delete()
            except Exception: pass
            await q.message.reply_text(f"⚠️ Gagal sinkron: {exc}")
            context.user_data.clear()
            return ConversationHandler.END
        try: await busy.delete()
        except Exception: pass
        usage.log(update.effective_chat.id, users.get(update.effective_chat.id) or "",
                  "sinkron", f"{kode} p.{'+'.join(map(str, nums))}")
        await q.message.reply_text(
            f"✅ Sinkron selesai: <b>{n_sf} SF→S</b>, <b>{n_of} OF→O</b> (sheet Absen). "
            "Status didasarkan pada fakta feedback form.",
            parse_mode=ParseMode.HTML)
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Dibatalkan.")
    return ConversationHandler.END


async def timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text("⏱ Sinkron dibatalkan (timeout 10 menit). Kirim /sinkron lagi.")
    return ConversationHandler.END


def register(app: Application, cfg: Config) -> None:
    conv = ConversationHandler(
        entry_points=[CommandHandler("sinkron", cmd_sinkron)],
        states={
            CONFIRM: [CallbackQueryHandler(confirm_cb, pattern=r"^skn:(ok|no)$")],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, timeout)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=_TIMEOUT,
        name="sinkron_conv",
    )
    app.add_handler(conv)