"""/stats — admin only: who used bot."""
from __future__ import annotations

from collections import Counter, defaultdict

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

import usage
import users

ADMIN_ID = 2061872254

MENU_LABEL = {"zoom": "Zoom", "absen": "Absen", "backup": "Backup",
              "cancel": "Cancel", "rekap": "Rekap"}
MENU_ORDER = ["zoom", "absen", "rekap", "backup", "cancel"]


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ADMIN_ID:
        await update.message.reply_text("⛔ Hanya admin bisa lihat stats.")
        return
    from telegram.constants import ChatAction
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    except Exception:
        pass
    busy = await update.message.reply_text("⏳ Hitung statistik...")
    all_users = users.registry().all() if users.registry() else {}
    data, _, _ = usage.stats()

    per_menu = Counter(e["action"] for e in data)
    per_user = defaultdict(Counter)
    last_ts: dict[str, str] = {}
    for e in data:
        per_user[e["name"]][e["action"]] += 1
        last_ts[e["name"]] = e["ts"]

    active = sorted(per_user, key=lambda n: (-sum(per_user[n].values()), n))
    inactive = sorted(set(all_users.values()) - set(per_user))

    L = ["📊 <b>STATS BOT</b>",
         f"👥 {len(all_users)} terdaftar | ⚡ {len(data)} aksi via bot",
         "",
         "<b>Per menu</b>"]
    if per_menu:
        for m in MENU_ORDER + sorted(set(per_menu) - set(MENU_ORDER)):
            if m in per_menu:
                L.append(f"• {MENU_LABEL.get(m, m)} — {per_menu[m]}x")
    else:
        L.append("• (belum ada aksi)")
    L += ["", "<b>Paling aktif</b>"]
    if active:
        for i, n in enumerate(active[:7], 1):
            tot = sum(per_user[n].values())
            acts = " ".join(f"{MENU_LABEL.get(k, k)}{v}" for k, v in sorted(per_user[n].items()))
            ts = last_ts[n][:16].replace("T", " ")
            L.append(f"{i}. {n} — {tot}x ({acts})")
            L.append(f"   <i>terakhir {ts}</i>")
        if len(active) > 7:
            L.append(f"   <i>+{len(active) - 7} lainnya</i>")
    else:
        L.append("• (belum ada)")
    if inactive:
        L += ["", f"<b>Belum pernah pakai ({len(inactive)})</b>"]
        L.append("• " + ", ".join(sorted(inactive)[:10]) + (" ..." if len(inactive) > 10 else ""))
    try:
        rows = await context.bot_data["sheets"].sheet_rows(context.bot_data["cfg"].zoom_record_sheet)
        c = Counter(r[2].strip() for r in rows[1:] if len(r) > 2 and r[2].strip())
        if c:
            L += ["", f"<b>Sheet Zoom Record</b> — {sum(c.values())} baris"]
            for n, cnt in c.most_common(5):
                L.append(f"• {n}: {cnt}")
    except Exception:
        L.append("\n⚠️ Sheet gagal dibaca (sementara).")
    try:
        await busy.delete()
    except Exception:
        pass
    await update.message.reply_text("\n".join(L), parse_mode=ParseMode.HTML)


def register(app: Application, cfg) -> None:
    app.add_handler(CommandHandler("stats", stats_cmd))
