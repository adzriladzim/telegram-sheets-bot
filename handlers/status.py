"""Shared status indicators: typing action + temporary loading message."""
from __future__ import annotations

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes


async def typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass


async def loading(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str = "⏳ Harap tunggu..."):
    """Show typing + send a temporary loading message. Returns the message (or None)."""
    await typing(context, update.effective_chat.id)
    try:
        return await update.effective_message.reply_text(text)
    except Exception:
        return None


async def unbusy(msg) -> None:
    if msg is None:
        return
    try:
        await msg.delete()
    except Exception:
        pass


async def saving(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str = "⏳ Menyimpan ke sheet..."):
    """Show typing + saving message for callback-query flows. Returns the message (or None)."""
    q = update.callback_query
    await typing(context, update.effective_chat.id)
    try:
        return await q.message.reply_text(text)
    except Exception:
        return None
