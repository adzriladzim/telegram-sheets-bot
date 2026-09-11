"""Bot usage tracking."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter

WIB = timezone(timedelta(hours=7))

def _path() -> Path:
    from config import BASE_DIR
    return BASE_DIR / "data" / "usage.json"

def log(chat_id: int, name: str, action: str, kode: str):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except: data = []
    data.append({"chat_id": chat_id, "name": name, "action": action, "kode": kode, "ts": datetime.now(WIB).isoformat()})
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def stats():
    p = _path()
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except: data = []
    c = Counter()
    last = {}
    for e in data:
        c[e["name"]] += 1
        last[e["name"]] = e["ts"]
    return data, c, last
