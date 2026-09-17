"""Offline stub — /stats tombol inline (st:* callback). No live creds.

Run: py verify_stats_callback.py   (expect: ALL PASS)

Checks:
  1. _mode_from_cb: st:detail -> lengkap, st:ringkas -> ringkas,
     st:refresh:<mode> -> mode sama, data lain/None -> None.
  2. _keyboard: 3 tombol — Detail/Ringkas/Refresh; Refresh bawa mode.
  3. stats_callback non-admin: q.answer(show_alert) + laporan TIDAK dihitung.
  4. stats_callback admin: answer() dulu, `_generate_report` dipanggil dgn mode
     dari callback data, busy sebagai reply pesan baru, tombol di chunk
     TERAKHIR (bukan chunk pertama).
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import handlers.stats as stats

PASS, FAIL = [], []


def check(label, cond, msg=""):
    (PASS if cond else FAIL).append((label, msg))
    print(("PASS " if cond else "FAIL ") + label + (f" — {msg}" if msg and not cond else ""))


# ---------- 1. callback data -> mode ----------
check("cb st:detail -> lengkap", stats._mode_from_cb("st:detail") == "lengkap")
check("cb st:ringkas -> ringkas", stats._mode_from_cb("st:ringkas") == "ringkas")
check("cb st:refresh:ringan -> ringan", stats._mode_from_cb("st:refresh:ringan") == "ringan")
check("cb st:refresh:ringkas -> ringkas", stats._mode_from_cb("st:refresh:ringkas") == "ringkas")
check("cb st:refresh:lengkap -> lengkap", stats._mode_from_cb("st:refresh:lengkap") == "lengkap")
check("cb st:bogus -> None", stats._mode_from_cb("st:bogus") is None)
check("cb st:refresh (no mode) -> None", stats._mode_from_cb("st:refresh") is None)
check("cb None -> None", stats._mode_from_cb(None) is None)

# ---------- 2. keyboard ----------
kb = stats._keyboard("ringkas")
rows = kb.inline_keyboard
cbs = [b.callback_data for row in rows for b in row]
check("keyboard punya 3 tombol", len(cbs) == 3, f"got {cbs!r}")
check("keyboard ada Detail/Ringkas/Refresh",
      "st:detail" in cbs and "st:ringkas" in cbs and "st:refresh:ringkas" in cbs, f"got {cbs!r}")
check("keyboard refresh bawa mode sama", any(b.text == "🔄 Refresh" and b.callback_data == "st:refresh:ringkas"
                                            for row in rows for b in row))

# ---------- 3+4. callback handler ----------
class FakeBot:
    def __init__(self):
        self.typing = []

    async def send_chat_action(self, chat_id, action):
        self.typing.append((chat_id, action))


class FakeAnswerLog:
    def __init__(self):
        self.calls = []

    async def __call__(self, text=None, show_alert=False):
        self.calls.append((text, show_alert))


class FakeQ:
    def __init__(self, data, answer_log):
        self.data = data
        self._answer_log = answer_log
        self.message: FakeMsg | None = None

    async def answer(self, text=None, show_alert=False):
        await self._answer_log(text, show_alert)


class FakeMsg:
    def __init__(self, reply_log):
        self.reply_log = reply_log
        self.replies = []

    async def reply_text(self, *a, **kw):
        self.replies.append((a, kw))
        return self

    async def delete(self):
        pass


async def run_callback(chat_id, cb_data, stopped=False):
    ans = FakeAnswerLog()
    q = FakeQ(cb_data, ans)
    msg = FakeMsg(None)
    q.message = msg
    bot = FakeBot()
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), callback_query=q)
    context = SimpleNamespace(bot=bot, bot_data={"sheets": None})
    original = stats._generate_report

    async def fake_generate(mode, days, ctx):
        nonlocal stopped
        stopped = "generate_called"

    stats._generate_report = fake_generate
    try:
        await stats.stats_callback(update, context)
    finally:
        stats._generate_report = original
    return ans, msg, bot, stopped


# non-admin: jawab dulu, tolak, tidak hitung
STATUS = {}
ans, msg, bot, stopped = None, None, None, None
import asyncio
ans, msg, bot, stopped = asyncio.get_event_loop().run_until_complete(run_callback(1, "st:ringkas"))
check("non-admin answer() dipanggil", len(ans.calls) >= 1)
check("non-admin tolak show_alert",
      any(txt == "⛔ Hanya admin bisa lihat stats." and alert for txt, alert in ans.calls), f"got {ans.calls!r}")
check("non-admin TIDAK hitung laporan", stopped is not True, f"got {stopped!r}")

# admin refresh lengkap: generate_mode == lengkap, busy reply pesan baru, tombol di chunk terakhir
GENERATED = {}


async def capture_generate(mode, days, ctx):
    GENERATED["mode"] = mode
    GENERATED["days"] = days
    # dua baris panjang (~2000b tiap) — _chunks pecah jadi 2 chunk
    return ["a" * 2000, "b" * 2000]


import handlers.stats as _s
_orig = _s._generate_report
_s._generate_report = capture_generate
STATUS2 = {}
async def _run_admin():
    ans = FakeAnswerLog()
    q = FakeQ("st:refresh:lengkap", ans)
    msg = FakeMsg(None)
    q.message = msg
    bot = FakeBot()
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=stats.ADMIN_ID), callback_query=q)
    context = SimpleNamespace(bot=bot, bot_data={"sheets": None})
    await _s.stats_callback(update, context)
    return ans, msg, bot

ans, msg, bot = asyncio.get_event_loop().run_until_complete(_run_admin())
_s._generate_report = _orig
check("admin answer() dulu (tanpa alert)", len(ans.calls) == 1 and ans.calls[0] == (None, False), f"got {ans.calls!r}")
check("admin generate mode dari callback (lengkap)", GENERATED.get("mode") == "lengkap", f"got {GENERATED!r}")
check("admin busy sebagai reply pesan baru",
      msg.replies and msg.replies[0][1].get("parse_mode") is None and "Hitung statistik" in msg.replies[0][0][0],
      f"got {[r[0][0][:30] for r in msg.replies] if msg.replies else None!r}")
n = len(msg.replies)
assert n >= 3, f"expected busy+2chunks, got {n}"
first, last = msg.replies[1][1].get("reply_markup"), msg.replies[-1][1].get("reply_markup")
check("chunk pertama TANPA tombol", first is None, f"got {first!r}")
check("chunk terakhir DENGAN tombol", last is not None
      and any(b.callback_data == "st:refresh:lengkap" for row in last.inline_keyboard for b in row),
      f"got {last}")

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL"))
sys.exit(1 if FAIL else 0)