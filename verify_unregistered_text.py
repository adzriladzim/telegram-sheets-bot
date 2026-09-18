"""Stub verify: onboarding "cukup ketik nama aja" — text_unregistered group 1 + rgn picker.
Run: py verify_unregistered_text.py   (expect: ALL PASS)

Menjalankan Application.process_update sungguhan (PTB 22.8) → membuktikan urutan
group 0 (ConversationHandler) lalu group 1 (text_unregistered) tanpa menabrak state.
"""
from __future__ import annotations

import asyncio
import datetime
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # target py3.7+

from telegram import Bot, CallbackQuery, Chat, Message, MessageEntity, Update, User
from telegram.ext import Application

import sheets  # noqa: F401  (SheetsError di-import oleh handlers.register)
import users
import importlib
reg = importlib.import_module("handlers.register")  # by-passes handlers.__init__ shadow

FAIL = []


def check(label: str, cond: bool, msg: str = "") -> None:
    if not cond:
        FAIL.append(label)
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))


# ---------- bot tanpa network ----------
class FakeMsg:
    def __init__(self) -> None:
        self.deleted = False

    async def delete(self) -> None:
        self.deleted = True


class FakeSheets:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def find_facilitator_names(self, search: str) -> list[str]:
        self.calls.append(search)
        table = {
            "Adzril": ["Adzril Adzim Hendrynov"],
            "Budi": ["Budi Santoso"],
            "Dewi": ["Dewi Lestari", "Dewi Anggraini"],
        }
        return table.get(search, [])


SENT: list[str] = []


class FakeBot:
    """Duck-typed Bot: CommandHandler butuh .username, reply_text butuh .defaults/.send_message."""
    username = "TestFasilBot"
    defaults = None

    async def send_chat_action(self, *args, **kwargs) -> None:
        return None

    async def send_message(self, *args, **kwargs) -> FakeMsg:
        SENT.append(str(kwargs.get("text", "")))
        return FakeMsg()

    async def edit_message_text(self, *args, **kwargs) -> None:
        SENT.append(f"[edit] {kwargs.get('text', '')}")
        return None

    async def answer_callback_query(self, *args, **kwargs) -> None:
        return None


FAKE_BOT = FakeBot()

async def _fake_send_message(*args, **kwargs) -> FakeMsg:
    SENT.append(str(kwargs.get("text", "")))
    return FakeMsg()


async def _noop(*args, **kwargs) -> None:
    return None


async def _fake_edit_text(*args, **kwargs) -> None:
    SENT.append(f"[edit] {kwargs.get('text', '')}")
    return None


Bot.send_message = _fake_send_message
Bot.send_chat_action = _noop
Bot.answer_callback_query = _noop
Bot.edit_message_text = _fake_edit_text

_now = datetime.datetime.now(tz=datetime.timezone.utc)


def make_update(chat_id: int, text: str, entity=None, mid: int = 1) -> Update:
    chat = Chat(id=chat_id, type="private")
    user = User(id=chat_id, first_name="Fasil", is_bot=False)
    msg = Message(
        message_id=mid, date=_now, chat=chat, from_user=user,
        text=text, entities=([entity] if entity else None),
    )
    return Update(update_id=mid, message=msg)


def make_cb(chat_id: int, data: str, mid: int = 2) -> Update:
    chat = Chat(id=chat_id, type="private")
    user = User(id=chat_id, first_name="Fasil", is_bot=False)
    msg = Message(message_id=mid, date=_now, chat=chat, from_user=user, text="pilih")
    cq = CallbackQuery(id=str(mid), from_user=user, chat_instance=str(chat_id), message=msg, data=data)
    return Update(update_id=100 + mid, callback_query=cq)


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    users.init(tmp / "users.json")

    app = (
        Application.builder()
        .token("123456:TEST-TOKEN")
        .connect_timeout(1)
        .read_timeout(1)
        .build()
    )
    # bypass initialize() (berisiko nge-net) — kita tes process_update langsung
    app._check_initialized = lambda: None  # type: ignore[method-assign]
    app.bot_data["sheets"] = FakeSheets()
    app.bot_data["cfg"] = SimpleNamespace(  # reminder/heartbeat read cfg sebelum job_queue check
        reminder_enabled=False, heartbeat_enabled=False,
        reminder_slots=[], heartbeat_hour=5, heartbeat_minute=0,
    )
    reg.register(app, None)  # cfg tidak dipakai di body register()

    def send(update: Update) -> None:
        update.set_bot(FAKE_BOT)  # Update._bot utk context.bot (duck bot, offline)
        # message/callback juga butuh _bot utk shortcut reply_text/edit_text/answer
        if update.callback_query:
            update.callback_query._bot = FAKE_BOT
            m = update.callback_query.message
        else:
            m = update.message
        if m is not None:
            m._bot = FAKE_BOT
        app.update_queue.put_nowait(update)

    # 1. Unregistered text → lookup → registered (1 match)
    send(make_update(200, "Adzril"))
    await app.process_update(await app.update_queue.get())
    check("unregistered text → lookup 1x", app.bot_data["sheets"].calls.count("Adzril") == 1,
          f"calls={app.bot_data['sheets'].calls}")
    check("unregistered text → saved", users.get(200) == "Adzril Adzim Hendrynov",
          f"got={users.get(200)!r}")
    check("replies ✅ Terdaftar", any("✅ Terdaftar sebagai" in s for s in SENT))

    # 2. Registered user text → TIDAK diganggu (tanpa lookup, tanpa reply)
    before2 = len(SENT)
    send(make_update(200, "test pesan apa aja"))
    await app.process_update(await app.update_queue.get())
    check("registered text → tidak lookup", app.bot_data["sheets"].calls.count("test pesan apa aja") == 0)
    check("registered text → tidak reply", before2 == len(SENT))

    # 3. Conversation /register aktif di group 0 tetap menang (flag WAITING)
    send(make_update(300, "/register", entity=MessageEntity(type="bot_command", offset=0, length=9)))
    await app.process_update(await app.update_queue.get())
    check("/register tanpa arg → prompt nama", any("Ketik nama fasilitator" in s for s in SENT))
    send(make_update(300, "Budi"))
    await app.process_update(await app.update_queue.get())
    check("conversation aktif → lookup sekali, bukan dobel",
          app.bot_data["sheets"].calls.count("Budi") == 1)
    check("conversation aktif → terdaftar via /register", users.get(300) == "Budi Santoso")

    # 3b. not_found di tengah /register → group 1 tidak dobel lookup (guard REG_JUST)
    send(make_update(310, "/register", entity=MessageEntity(type="bot_command", offset=0, length=9)))
    await app.process_update(await app.update_queue.get())
    send(make_update(310, "Orang Tak Dikenal"))
    await app.process_update(await app.update_queue.get())
    check("not_found /register → lookup sekali, bukan dobel",
          app.bot_data["sheets"].calls.count("Orang Tak Dikenal") == 1,
          f"count={app.bot_data['sheets'].calls.count('Orang Tak Dikenal')}")

    # 4. Banyak match → tombol rgn: tanpa /register
    send(make_update(400, "Dewi"))
    await app.process_update(await app.update_queue.get())
    send(make_cb(400, "rgn:1"))
    await app.process_update(await app.update_queue.get())
    check("banyak match → pilih rgn:1 tanpa /register", users.get(400) == "Dewi Anggraini",
          f"got={users.get(400)!r}")

    # 5. 0 match → pesan ramah + saran contoh
    before5 = len(SENT)
    send(make_update(500, "Zzz Tidak Ada"))
    await app.process_update(await app.update_queue.get())
    nf = [s for s in SENT[before5:] if "tidak ditemukan" in s]
    check("0 match → pesan minta lengkap", any("lebih lengkap" in s for s in nf), str(nf))
    check("0 match → saran contoh", any("Adzril Adzim Hendrynov" in s for s in SENT[before5:]))
    check("0 match → tidak terdaftar", users.get(500) is None)

    # 6. /start clue "cukup ketik namamu saja"
    from handlers import start
    src = Path(start.__file__).read_text(encoding="utf-8")
    check("/start clue ketik nama", "cukup ketik namamu saja" in src)

    # 7. Command /whatever tidak memicu lookup
    send(make_update(600, "/whatever", entity=MessageEntity(type="bot_command", offset=0, length=9)))
    await app.process_update(await app.update_queue.get())
    check("command /whatever → tidak lookup", app.bot_data["sheets"].calls.count("/whatever") == 0)

    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}"))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())