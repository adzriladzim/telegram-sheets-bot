"""Stub verify for stats._chunks fix (tag-split + byte cap). Run: py verify_stats_chunks.py"""
from __future__ import annotations
import re

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
import handlers.stats as stats
from telegram.constants import ParseMode

TAG = re.compile(r"</?[a-zA-Z][^>]*>")


def fake_parse(text: str) -> None:
    """Emulate Telegram's strict HTML parser: any stray '<' (e.g. '<3' or '<b')
    that isn't a well-formed tag raises. Mirrors the live BadRequest."""
    rest = TAG.sub("", text)
    if "<" in rest or ">" in rest:
        raise ValueError(f"BadRequest: Can't parse entities: stray HTML at {text!r}")


def check_parseable(chunks) -> None:
    for i, (part, pm) in enumerate(chunks):
        assert stats._b(part) <= 3500, f"chunk {i} over byte cap: {stats._b(part)}"
        assert stats._b(part) < 4096, f"chunk {i} over Telegram 4096 hard cap"
        if pm == ParseMode.HTML:
            fake_parse(part)  # tag never cut mid-line → always parseable
        else:
            assert "<" not in part and ">" not in part, f"plain chunk {i} has HTML"


# 1. report-like lines, emoji-heavy (~2x multi-byte density)
emoji_line = "• fasil nama — zoom 5 absen 3 rekap 2 backup 1 cancel 0, terakhir 🗓️ 01/01/2026 12:00 " + "🎉" * 1200
lines = ["📊 <b>STATS BOT</b>", emoji_line, "<b>Tunggakan</b>", f"• <b>{'x' * 500}</b> — " + "y" * 500]
chunks = stats._chunks(lines)
assert len(chunks) >= 2
check_parseable(chunks)
print(f"OK case1 emoji: {len(chunks)} chunks, max bytes={max(stats._b(p) for p, _ in chunks)}")

# 2. giant single line (> cap): tags stripped → plain, byte-split
giant = "<b>RAKSASA</b> " + "🥵" * 4000
chunks = stats._chunks([giant])
assert all(pm is None for _, pm in chunks), "giant line must be plain"
check_parseable(chunks)
print(f"OK case2 giant: {len(chunks)} plain chunks, max bytes={max(stats._b(p) for p, _ in chunks)}")

# 3. tiny lines still pack into one HTML chunk
chunks = stats._chunks(["<b>A</b>", "<b>B</b>"])
assert len(chunks) == 1 and chunks[0][1] == ParseMode.HTML
check_parseable(chunks)
print("OK case3 pack: single HTML chunk")

print("ALL PASS")