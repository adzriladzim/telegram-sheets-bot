"""/darurat — toggle mode darurat Online untuk Reguler."""
from __future__ import annotations

import json
import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import BASE_DIR, Config

log = logging.getLogger(__name__)

_ADMIN_ID_DEFAULT = 2061872254

DARURAT_FILE = BASE_DIR / "data" / "darurat.json"

def _admin_ids(context) -> tuple[int, ...]:
    """Admin ids: cfg.admin_ids (env ADMIN_IDS) bila ada, else default aman."""
    cfg = context.bot_data.get("cfg") if getattr(context, "bot_data", None) else None
    return getattr(cfg, "admin_ids", None) or (_ADMIN_ID_DEFAULT,)

def is_darurat() -> bool:
    try:
        return bool(json.loads(DARURAT_FILE.read_text(encoding="utf-8")).get("darurat"))
    except Exception: return False

def set_darurat(v: bool):
    DARURAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    DARURAT_FILE.write_text(json.dumps({"darurat": v}), encoding="utf-8")

async def darurat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = getattr(update, "effective_user", None)
    c = getattr(update, "effective_chat", None)
    uid = getattr(u, "id", None)
    uname = getattr(u, "username", None)
    cid = getattr(c, "id", None)
    admin_ids = _admin_ids(context)
    if uid not in admin_ids and cid not in admin_ids:
        log.warning("darurat denied chat=%s user=%s (@%s)", cid, uid, uname)
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
