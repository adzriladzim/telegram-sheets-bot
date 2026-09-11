"""Multi-user registry: Telegram chat_id → facilitator name. Persisted to data/users.json."""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)

UNREGISTERED_MSG = "⚠️ Belum terdaftar. Ketik /register <nama> dulu.\nContoh: /register Budi Santoso"


class UserRegistry:
    def __init__(self, path: Path, default_name: str = "") -> None:
        self.path = path
        self.default_name = default_name.strip()
        self._lock = threading.Lock()
        self._users: dict[int, str] = {}
        self._load()

    # ---------- persistence ----------

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._users = {int(k): str(v).strip() for k, v in raw.items() if str(v).strip()}
        except (OSError, ValueError, AttributeError):
            self._users = {}
        self._migrate_legacy_chats()

    def _migrate_legacy_chats(self) -> None:
        """Single-user → multi-user: give chats.json chat ids the default facilitator name."""
        if not self.default_name:
            return
        try:
            ids = [int(x) for x in json.loads((self.path.parent / "chats.json").read_text(encoding="utf-8"))]
        except (OSError, ValueError):
            return
        changed = False
        for cid in ids:
            if cid not in self._users:
                self._users[cid] = self.default_name
                changed = True
        if changed:
            self._save()
            log.info("Migrated %d legacy chat(s) to default facilitator %r", len(ids), self.default_name)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        payload = {str(k): v for k, v in sorted(self._users.items())}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ---------- API ----------

    def get(self, chat_id: int) -> str | None:
        return self._users.get(int(chat_id))

    def set(self, chat_id: int, name: str) -> None:
        with self._lock:
            self._users[int(chat_id)] = name.strip()
            self._save()

    def all(self) -> dict[int, str]:
        return dict(self._users)


_registry: UserRegistry | None = None


def init(path: Path, default_name: str = "") -> UserRegistry:
    """Create the process-wide registry. Call once at startup (bot.py)."""
    global _registry
    _registry = UserRegistry(path, default_name)
    log.info("User registry loaded: %d user(s) from %s", len(_registry.all()), path.name)
    return _registry


def registry() -> UserRegistry:
    if _registry is None:
        raise RuntimeError("users.init() not called")
    return _registry


def get(chat_id: int) -> str | None:
    return registry().get(chat_id)


def registered_chat_ids() -> list[int]:
    return sorted(registry().all())
