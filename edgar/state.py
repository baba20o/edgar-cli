"""Persistent high-water-mark state at ~/.edgar/state.json.

Keyed by (cik, form). Tracks the last-seen accession and filing date so
commands supporting --since-last-fetch can resume from where they left off.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

DEFAULT_STATE_PATH = "~/.edgar/state.json"
STATE_VERSION = 1


def _key(cik: str, form: Optional[str]) -> str:
    form = (form or "*").upper()
    return f"{cik}:{form}"


class StateStore:
    """Load/update a JSON file of high-water marks per (cik, form)."""

    def __init__(self, path: str = DEFAULT_STATE_PATH):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": STATE_VERSION, "last_seen": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "last_seen" not in data:
                return {"version": STATE_VERSION, "last_seen": {}}
            return data
        except (json.JSONDecodeError, OSError):
            return {"version": STATE_VERSION, "last_seen": {}}

    def _save(self) -> None:
        fd, tmp_path = tempfile.mkstemp(prefix="state.", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, default=str, indent=2)
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def get_high_water(self, cik: str, form: Optional[str] = None) -> Optional[dict]:
        """Return the last-seen entry for (cik, form), or None if absent."""
        return self._data.get("last_seen", {}).get(_key(cik, form))

    def update_high_water(self, cik: str, form: Optional[str], accession: str,
                          filed: str) -> None:
        """Store accession + filed date as the new high-water mark."""
        if not accession or not filed:
            return
        existing = self.get_high_water(cik, form)
        if existing and existing.get("filed", "") >= filed:
            return
        self._data.setdefault("last_seen", {})[_key(cik, form)] = {
            "accession": accession,
            "filed": filed,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._save()

    def subscriptions(self) -> list[dict]:
        return list(self._data.get("subscriptions", []))

    def subscribe(self, cik: str, form: Optional[str] = None) -> dict:
        """Register interest in (cik, form). Idempotent."""
        subs = self._data.setdefault("subscriptions", [])
        key = _key(cik, form)
        for sub in subs:
            if sub.get("key") == key:
                return sub
        entry = {"key": key, "cik": cik, "form": form or "*",
                 "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        subs.append(entry)
        self._save()
        return entry

    def unsubscribe(self, cik: str, form: Optional[str] = None) -> bool:
        subs = self._data.get("subscriptions", [])
        key = _key(cik, form)
        for i, sub in enumerate(subs):
            if sub.get("key") == key:
                subs.pop(i)
                self._save()
                return True
        return False

    def mark_seen(self, cik: str, form: Optional[str], accession: str, filed: str) -> None:
        """Alias for update_high_water — mark a filing as processed."""
        self.update_high_water(cik, form, accession, filed)

    def reset(self, cik: Optional[str] = None, form: Optional[str] = None) -> int:
        """Reset state for a (cik, form) pair, all forms for a cik, or everything."""
        last_seen = self._data.get("last_seen", {})
        if cik is None:
            removed = len(last_seen)
            self._data["last_seen"] = {}
            self._save()
            return removed
        if form is not None:
            removed = 1 if last_seen.pop(_key(cik, form), None) else 0
            if removed:
                self._save()
            return removed
        prefix = f"{cik}:"
        keys = [k for k in last_seen if k.startswith(prefix)]
        for k in keys:
            last_seen.pop(k, None)
        if keys:
            self._save()
        return len(keys)
