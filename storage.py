"""
Persistent user data storage using a JSON file.
Stores per-user OLX cookies and proxy settings keyed by Telegram user ID.
"""

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


class UserStorage:
    def __init__(self, storage_file: str = "data/users.json"):
        self._file = storage_file
        os.makedirs(os.path.dirname(storage_file) or ".", exist_ok=True)
        self._data: dict[str, Any] = self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if os.path.exists(self._file):
            try:
                with open(self._file, encoding="utf-8") as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Storage load failed: %s", exc)
        return {}

    def _save(self) -> None:
        try:
            with open(self._file, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.error("Storage save failed: %s", exc)

    def _user(self, uid: int) -> dict:
        return self._data.setdefault(str(uid), {})

    # ── Cookie ────────────────────────────────────────────────────────────────

    def set_cookie(self, uid: int, cookie: str) -> None:
        self._user(uid)["cookie"] = cookie
        self._save()

    def get_cookie(self, uid: int) -> Optional[str]:
        return self._data.get(str(uid), {}).get("cookie")

    # ── Proxy ─────────────────────────────────────────────────────────────────

    def set_proxy(self, uid: int, proxy: Optional[str]) -> None:
        u = self._user(uid)
        if proxy:
            u["proxy"] = proxy
        else:
            u.pop("proxy", None)
        self._save()

    def get_proxy(self, uid: int) -> Optional[str]:
        return self._data.get(str(uid), {}).get("proxy")

    # ── Full user record ──────────────────────────────────────────────────────

    def get_user(self, uid: int) -> dict:
        return dict(self._data.get(str(uid), {}))
