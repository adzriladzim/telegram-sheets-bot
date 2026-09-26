"""Offline verify — usage.json retention > 90 hari + atomic write (usage.py).

No network. Bekal:
  1. entri >90 hari dibuang saat log(), entri baru + entri muda dipertahankan
  2. entri tanpa ts valid / non-dict dipertahankan (jangan buang data tak terukur)
  3. atomic write: file result valid JSON, TIDAK ada sisa *.json.tmp
  4. log warning saat trim banyak entri
Run: py scripts/verify/verify_usage_retention.py
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root
sys.stdout.reconfigure(encoding="utf-8")

import usage

PASS, FAIL = [], []


def check(label, cond, msg=""):
    (PASS if cond else FAIL).append((label, msg))
    print(("PASS " if cond else "FAIL ") + label + (f" - {msg}" if msg and not cond else ""))


_tmp = Path(tempfile.mkdtemp(prefix="usage_verify_"))
_tmp_file = _tmp / "usage.json"
_orig_path = usage._path


def _seed():
    now = datetime.now(usage.WIB)
    old = (now - timedelta(days=100)).isoformat()   # > 90 hari -> dibuang
    mid = (now - timedelta(days=30)).isoformat()    # muda -> dipertahankan
    bad_ts = "bukan-timestamp"
    _tmp_file.write_text(json.dumps([
        {"chat_id": 1, "name": "A", "action": "zoom", "kode": "X1", "ts": old},
        {"chat_id": 2, "name": "B", "action": "absen", "kode": "X2", "ts": mid},
        {"chat_id": 3, "name": "C", "action": "rekap", "kode": "X3", "ts": bad_ts},
        "corrupt-row",
    ], ensure_ascii=False, indent=2), encoding="utf-8")


# tangkap warning trim
_warns = []


class _Rec(logging.Handler):
    def emit(self, record):
        _warns.append(record.getMessage())


rh = _Rec()
_lg = logging.getLogger("usage")
_lg.addHandler(rh)
_lg.setLevel(logging.WARNING)

try:
    usage._path = lambda: _tmp_file
    _seed()
    usage.log(999, "D", "backup", "X4")

    data = json.loads(_tmp_file.read_text(encoding="utf-8"))
    kodes = [e["kode"] for e in data if isinstance(e, dict)]
    check("entri >90 hari dibuang", "X1" not in kodes, str(kodes))
    check("entri muda dipertahankan", "X2" in kodes, str(kodes))
    check("entri ts invalid dipertahankan", "X3" in kodes, str(kodes))
    check("entri non-dict dipertahankan", any(not isinstance(e, dict) for e in data), str(data))
    check("entri baru tertulis", "X4" in kodes, str(kodes))
    check("file valid JSON + urutan terjaga", len(data) == 4, f"len={len(data)} data={data}")
    check("atomic: tanpa sisa *.json.tmp", not list(_tmp.glob("*.json.tmp")), str(list(_tmp.glob("*"))))
    check("warning trim ter-log (1 entri dibuang)", any("retention" in w for w in _warns), str(_warns))

    # kasus file belum ada -> log() bikin baru, tidak crash
    fresh = _tmp / "fresh.json"
    usage._path = lambda: fresh
    usage.log(1, "E", "zoom", "Y1")
    check("file baru dibuat, 1 entri", len(json.loads(fresh.read_text(encoding="utf-8"))) == 1)
finally:
    usage._path = _orig_path
    _lg.removeHandler(rh)
    shutil.rmtree(_tmp, ignore_errors=True)

print(f"\nRESULT PASS={len(PASS)} FAIL={len(FAIL)}")
sys.exit(1 if FAIL else 0)