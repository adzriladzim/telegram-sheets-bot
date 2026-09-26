"""Stub verify: _build_report escapes dynamic values + threshold label has no raw '<'.
Run: py verify_stats_html.py"""
from __future__ import annotations
import re
from collections import Counter, defaultdict
from types import SimpleNamespace

from telegram.constants import ParseMode

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
import handlers.stats as stats

TAG = re.compile(r"</?(?:b|i|code|br|u|s)[^>]*>|</?(?:b|i|code|br|u|s)>", re.I)


def fake_parse(text: str) -> None:
    """Emulate Telegram's strict HTML parser (stray '<' → BadRequest)."""
    rest = TAG.sub("", text)
    if "<" in rest or ">" in rest:
        raise ValueError(f"BadRequest: Can't parse entities: stray HTML at offset "
                         f"{text.index([c for c in ('<', '>') if c in rest][0])}: {text!r}")


EVIL = "Budi <script> & Co"
lines = stats._build_report(
    names=[EVIL, "Ani"],
    data=[{"name": EVIL, "action": "zoom", "ts": "2026-01-01T10:00:00"}],
    pdata=[{"name": EVIL, "action": "zoom", "ts": "2026-01-01T10:00:00"}],
    days=7,
    per_menu=Counter({"zoom": 1, "weird<tag": 2}),
    per_user=defaultdict(Counter, {EVIL: Counter({"zoom": 1})}),
    last_ts={EVIL: "2026-01-01T10:00:00"},
    matriks=[(EVIL, [("MTK<3", "✗", "○")])],
    arrears=[(EVIL, SimpleNamespace(code="MTK<3"), "Senin <1>", False)],
    cov={"MTK<3": ["a", "b"]},
)

blob = "\n".join(lines)
assert "kurang dari 3 aksi" in blob, "threshold label not reworded"
assert "&lt;script&gt;" in blob, "dynamic name not escaped"
assert "MTK&lt;3" in blob, "dynamic code not escaped"
assert "Senin &lt;1&gt;" in blob, "dynamic label not escaped"
assert "weird&lt;tag" in blob, "dynamic action not escaped"

for i, (part, pm) in enumerate(stats._chunks(lines)):
    if pm == ParseMode.HTML:
        fake_parse(part)
    else:
        assert "<" not in part and ">" not in part

print("OK case1 evil-name report: %d chunks, all parseable" % len(stats._chunks(lines)))

# 2. balance check: every remaining '<' opens/closes a known tag
opens = re.findall(r"<([a-zA-Z][^>]*)>", blob)
for t in opens:
    assert re.fullmatch(r"b|i|code|br|/(b|i|code|br)", t), f"unknown tag <{t}>"
assert len(re.findall(r"<b>", blob)) == len(re.findall(r"</b>", blob)), "b tags unbalanced"
assert len(re.findall(r"<i>", blob)) == len(re.findall(r"</i>", blob)), "i tags unbalanced"
print("OK case2 tag balance: b/i balanced, only known tags")

print("ALL PASS")
