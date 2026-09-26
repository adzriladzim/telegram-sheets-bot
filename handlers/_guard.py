"""Cross-handler conversation guard — S2 user_data collision fix.

Semua ConversationHandler (log, rekap, absen, backup, cancel, tukar, sinkron,
laporan, register) share satu `context.user_data` per (chat, user). Kalau dua
form aktif di chat yang sama, key yang sama (cls, meeting, facilitator, ...)
saling menimpa -> data form rusak. Guard ini mencegah DUA conversation aktif
bersamaan: tiap entry point cek registry, blokir kalau handler lain lagi jalan.
Re-entry handler yang SAMA tetap diteruskan ke state aktifnya (allow_reentry=True
konvensi repo; jangan reset form diam-diam).

Registry diisi tiap `register()` handler (sebelum polling jalan), jadi saat
update masuk semua conv sudah terdaftar.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

_REGISTRY: dict[str, ConversationHandler] = {}


def register_conv(name: str, conv: ConversationHandler) -> None:
    """Daftarkan conversation utk guard. Panggil di register() tiap handler."""
    _REGISTRY[name] = conv


def state_of(chat_id: int, user_id: int, conv) -> int | None:
    """State int kalau conv ini lagi aktif utk (chat, user), else None.
    END/TIMEOUT = tidak aktif. Exception-safe (verify/stub aman)."""
    try:
        state = conv._conversations.get((chat_id, user_id))
    except Exception:
        return None
    if state is None or state in (ConversationHandler.END, ConversationHandler.TIMEOUT):
        return None
    return state


def active_name(chat_id: int, user_id: int) -> str | None:
    """Nama handler yang conversation-nya aktif utk (chat, user), else None."""
    for name, conv in _REGISTRY.items():
        if state_of(chat_id, user_id, conv) is not None:
            return name
    return None


async def guard_entry(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    own: str,
    busy_label: str,
) -> int | None:
    """Guard entry point conversation. Return:
    - None  -> boleh lanjut mulai form
    - int   -> state aktif handler OWn (re-entry form yg sama: jangan reset)
    - ConversationHandler.END -> handler lain aktif: form ini diblockir

    `own` = nama registry conv ini; `busy_label` = perintah yg disandang user."""
    chat_id = update.effective_chat.id if update.effective_chat else None
    user_id = update.effective_user.id if update.effective_user else None
    if chat_id is None or user_id is None:
        return None
    own_conv = _REGISTRY.get(own)
    if own_conv is not None:
        st = state_of(chat_id, user_id, own_conv)
        if st is not None:
            return st
    if active_name(chat_id, user_id) is not None:
        msg = update.effective_message
        if msg is not None:
            try:
                await msg.reply_text(
                    "Kamu masih di tengah form lain — selesaikan atau ketik "
                    f"/cancel dulu, baru mulai /{busy_label}."
                )
            except Exception:
                pass
        return ConversationHandler.END
    return None