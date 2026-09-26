"""Offline verify — S2 user_data collision guard (handlers/_guard.py).

No creds, no telegram server. Bekal:
  1. register_conv + state_of: aktif -> state; END/TIMEOUT -> None
  2. active_name: handler lain yang aktif terdeteksi
  3. guard_entry: same-handler re-entry -> return state aktif (jangan reset form)
  4. guard_entry: handler lain aktif -> block END + pesan
  5. guard_entry: none aktif -> None (boleh mulai form)
  6. guard_entry None-safe (tanpa effective_chat/user -> None)
  7. log._active_log_state tetap jalan (regresi s2s3)
Run: py scripts/verify/verify_user_data_namespace.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
sys.stdout.reconfigure(encoding="utf-8")

from handlers import _guard
from handlers import log as h_log

PASS, FAIL = [], []


def check(label, cond, msg=""):
    (PASS if cond else FAIL).append((label, msg))
    print(("PASS " if cond else "FAIL ") + label + (f" - {msg}" if msg and not cond else ""))


class _FakeConv:
    """Mirip ConversationHandler: cuma butuh _conversations utk guard."""

    def __init__(self):
        self._conversations = {}


class _Msg:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


class _FakeUpdate:
    def __init__(self, chat=None, user=None):
        self.effective_chat = type("C", (), {"id": chat})() if chat is not None else None
        self.effective_user = type("U", (), {"id": user})() if user is not None else None
        self.effective_message = _Msg()


class _Ctx:
    pass


conv_a, conv_b = _FakeConv(), _FakeConv()

# simpan registry lama, reset, restore di akhir
_SAVED = dict(_guard._REGISTRY)
_guard._REGISTRY.clear()
try:
    _guard.register_conv("conv_a", conv_a)
    _guard.register_conv("conv_b", conv_b)

    # 1. state_of
    check("state_of: kosong -> None", _guard.state_of(1, 1, conv_a) is None)
    conv_a._conversations = {(1, 1): 3}
    check("state_of: aktif -> state 3", _guard.state_of(1, 1, conv_a) == 3)
    conv_a._conversations = {(1, 1): h_log.ConversationHandler.END}
    check("state_of: END -> None", _guard.state_of(1, 1, conv_a) is None)
    conv_a._conversations = {(1, 1): h_log.ConversationHandler.TIMEOUT}
    check("state_of: TIMEOUT -> None", _guard.state_of(1, 1, conv_a) is None)
    conv_a._conversations = {}

    # 2. active_name
    check("active_name: kosong -> None", _guard.active_name(1, 1) is None)
    conv_b._conversations = {(1, 1): 7}
    check("active_name: handler B aktif -> conv_b", _guard.active_name(1, 1) == "conv_b")
    conv_b._conversations = {}

    # 3. same-handler re-entry -> state aktif (tidak reset)
    conv_a._conversations = {(1, 1): 5}
    rc = asyncio.run(_guard.guard_entry(_FakeUpdate(1, 1), _Ctx(), "conv_a", "log"))
    check("re-entry same handler -> state aktif", rc == 5, f"rc={rc}")
    conv_a._conversations = {}

    # 4. handler lain aktif -> block END + pesan
    conv_b._conversations = {(1, 1): 7}
    upd = _FakeUpdate(1, 1)
    rc = asyncio.run(_guard.guard_entry(upd, _Ctx(), "conv_a", "log"))
    check("handler lain aktif -> END", rc == _guard.ConversationHandler.END, f"rc={rc}")
    check("handler lain aktif -> ada pesan block", len(upd.effective_message.replies) == 1,
          str(upd.effective_message.replies))
    conv_b._conversations = {}

    # 5. none aktif -> None (boleh mulai)
    rc = asyncio.run(_guard.guard_entry(_FakeUpdate(1, 1), _Ctx(), "conv_a", "log"))
    check("none aktif -> None (boleh mulai)", rc is None, f"rc={rc}")

    # 6. None-safe: tanpa chat/user
    rc = asyncio.run(_guard.guard_entry(_FakeUpdate(), _Ctx(), "conv_a", "log"))
    check("tanpa chat/user -> None", rc is None, f"rc={rc}")

    # 7. log._active_log_state tetap jalan
    class _FU:
        def __init__(self, chat, user):
            self.effective_chat = type("C", (), {"id": chat})()
            self.effective_user = type("U", (), {"id": user})()

    orig = h_log.LOG_CONV
    try:
        check("log guard: LOG_CONV None -> None", h_log._active_log_state(_FU(1, 1), None) is None)
        fc = _FakeConv()
        fc._conversations = {(1, 1): h_log.MEETING}
        h_log.LOG_CONV = fc
        check("log guard: aktif -> MEETING", h_log._active_log_state(_FU(1, 1), None) == h_log.MEETING)
    finally:
        h_log.LOG_CONV = orig
finally:
    _guard._REGISTRY.clear()
    _guard._REGISTRY.update(_SAVED)

print(f"\nRESULT PASS={len(PASS)} FAIL={len(FAIL)}")
sys.exit(1 if FAIL else 0)