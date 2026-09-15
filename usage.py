"""Bot usage tracking."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter

_log = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))

# Serializes read-modify-write (load -> append -> save) so concurrent updates
# (concurrent_updates=True) can't drop entries.
_lock = threading.Lock()

def _path() -> Path:
    from config import BASE_DIR
    return BASE_DIR / "data" / "usage.json"

def log(chat_id: int, name: str, action: str, kode: str, **extra):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        except Exception as exc:
            _log.warning("usage.json read failed: %s", exc)
            data = []
        entry = {"chat_id": chat_id, "name": name, "action": action, "kode": kode, "ts": datetime.now(WIB).isoformat()}
        # Backward-compat extra detail (e.g. absen pertemuan/status/jumlah).
        # Existing callers pass 4 args; action-specific extras only when provided.
        entry.update({k: v for k, v in extra.items() if v is not None and v != ""})
        data.append(entry)
        try:
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            # Fail-open: usage tracking must never break the main flow (/zoom etc.).
            _log.warning("usage.json write failed: %s", exc)

def stats():
    p = _path()
    with _lock:
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        except Exception as exc:
            _log.warning("usage.json read failed: %s", exc)
            data = []
    c = Counter()
    last = {}
    for e in data:
        c[e["name"]] += 1
        last[e["name"]] = e["ts"]
    return data, c, last