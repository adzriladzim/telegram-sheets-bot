"""/darurat — toggle mode darurat Online untuk Reguler."""
from __future__ import annotations

import json
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import BASE_DIR, Config

ADMIN_ID = 2061872254

DARURAT_FILE = BASE_DIR / "data" / "darurat.json"

def is_darurat() -> bool:
    try:
        return bool(json.loads(DARURAT_FILE.read_text(encoding="utf-8")).get("darurat"))
    except Exception: return False

def set_darurat(v: bool):
    DARURAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    DARURAT_FILE.write_text(json.dumps({"darurat": v}), encoding="utf-8")

async def darurat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ADMIN_ID:
        await update.message.reply_text("⛔ Hanya admin bisa pakai /darurat.")
        return
    args = (context.args or [])
    if not args:
        st = "ON 🟢 (Reguler → Online)" if is_darurat() else "OFF ⚪ (Reguler manual)"
        await update.message.reply_text(f"Mode darurat: {st}\n\n/darurat on — aktifkan\n/darurat off — matikan")
        return
    val = args[0].lower()
    if val in ("on","1","true","ya"):
        set_darurat(True)
        await update.message.reply_text("✅ Darurat ON — semua Reguler auto Online")
    elif val in ("off","0","false","tidak"):
        set_darurat(False)
        await update.message.reply_text("✅ Darurat OFF — Reguler pilih manual")
    else:
        await update.message.reply_text("Pakai: /darurat on | /darurat off")

def register(app: Application, cfg: Config) -> None:
    app.add_handler(CommandHandler("darurat", darurat_cmd))
