"""Persistent high-water-mark state at ~/.edgar/state.json.

Keyed by (cik, form). Tracks the last-seen accession and filing date so
commands supporting --since-last-fetch can resume from where they left off.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Optional

DEFAULT_STATE_PATH = "~/.edgar/state.json"
STATE_VERSION = 1

try:
    import fcntl
except ImportError:
    fcntl = None
try:
    import msvcrt
except ImportError:
    msvcrt = None

_STATE_LOCKS: dict[str, threading.RLock] = {}
_STATE_LOCKS_GUARD = threading.Lock()


def _serialized_update(method):
    """Reload while locked so independent stores never overwrite each other."""
    @wraps(method)
    def update(self, *args, **kwargs):
        with self._write_lock():
            self._data = self._load(strict=True)
            return method(self, *args, **kwargs)
    return update


def _feed_time(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("Feed cursor timestamps require a UTC offset")
    return timestamp.astimezone(timezone.utc)


def _key(cik: str, form: Optional[str]) -> str:
    form = (form or "*").upper()
    return f"{cik}:{form}"


class StateStore:
    """Load/update a JSON file of high-water marks per (cik, form)."""

    def __init__(self, path: str = DEFAULT_STATE_PATH):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self, strict: bool = False) -> dict:
        if not self.path.exists():
            return {"version": STATE_VERSION, "last_seen": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or not isinstance(data.get("last_seen"), dict)
                    or not isinstance(data.get("subscriptions", []), list)
                    or not isinstance(data.get("feed_cursors", {}), dict)):
                raise ValueError("Invalid state file structure")
            return data
        except (ValueError, OSError):
            if strict:
                raise
            return {"version": STATE_VERSION, "last_seen": {}}

    @contextmanager
    def _write_lock(self):
        """Required process/thread lock; failure never silently permits a write."""
        identity = str(self.path.resolve())
        with _STATE_LOCKS_GUARD:
            lock = _STATE_LOCKS.setdefault(identity, threading.RLock())
        with lock:
            lock_path = self.path.with_name(self.path.name + ".lock")
            with lock_path.open("a+b") as handle:
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                deadline = time.monotonic() + 10.0
                while True:
                    try:
                        if fcntl is not None:
                            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        elif msvcrt is not None:
                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            raise RuntimeError("No supported state-file locking primitive")
                        break
                    except (BlockingIOError, OSError):
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Timed out locking EDGAR state")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    elif msvcrt is not None:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    def _save(self) -> None:
        fd, tmp_path = tempfile.mkstemp(prefix="state.", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, default=str, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def get_high_water(self, cik: str, form: Optional[str] = None) -> Optional[dict]:
        """Return the last-seen entry for (cik, form), or None if absent."""
        self._data = self._load()
        return self._data.get("last_seen", {}).get(_key(cik, form))

    @_serialized_update
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
        self._data = self._load()
        return list(self._data.get("subscriptions", []))

    @_serialized_update
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

    @_serialized_update
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

    @_serialized_update
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


    def get_feed_cursor(self, scope: str) -> Optional[dict]:
        """Read a form-scope cursor independently of per-company subscriptions."""
        self._data = self._load(strict=True)
        cursor = self._data.get("feed_cursors", {}).get(scope)
        if cursor is None:
            return None
        if (not isinstance(cursor, dict) or not isinstance(cursor.get("timestamp"), str)
                or not isinstance(cursor.get("seen"), list)
                or not all(isinstance(item, str) for item in cursor["seen"])):
            raise ValueError("Invalid feed cursor structure")
        _feed_time(cursor["timestamp"])
        recent = cursor.get("recent_seen", {})
        if not isinstance(recent, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in recent.items()):
            raise ValueError("Invalid feed recent identity map")
        for timestamp in recent.values():
            _feed_time(timestamp)
        return {**cursor, "seen": list(cursor["seen"]), "recent_seen": dict(recent)}

    @_serialized_update
    def reset_feed_cursor(self, scope: str) -> bool:
        """Explicitly discard one feed cursor without changing other state."""
        if not isinstance(scope, str) or not scope:
            raise ValueError("Invalid feed cursor scope")
        cursors = self._data.get("feed_cursors", {})
        if scope not in cursors:
            return False
        del cursors[scope]
        self._save()
        return True

    @_serialized_update
    def update_feed_cursor(self, scope: str, timestamp: str, seen: list[str],
                           recent_seen: Optional[dict[str, str]] = None,
                           retention_from: Optional[str] = None) -> bool:
        """Acknowledge a scanned-through watermark and merge delivered overlap IDs."""
        if not isinstance(scope, str) or not scope or not isinstance(seen, list) or not all(isinstance(item, str) for item in seen):
            raise ValueError("Invalid feed cursor scope or identities")
        accepted = _feed_time(timestamp)
        existing = self.get_feed_cursor(scope)
        identities = set(seen)
        recent = dict(existing.get("recent_seen", {})) if existing else {}
        if recent_seen is not None:
            if not isinstance(recent_seen, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in recent_seen.items()):
                raise ValueError("Invalid feed recent identity map")
            for identity, value in recent_seen.items():
                recent[identity] = _feed_time(value).isoformat().replace("+00:00", "Z")
        if retention_from is not None:
            floor = _feed_time(retention_from)
            recent = {identity: value for identity, value in recent.items() if _feed_time(value) >= floor}
        if existing:
            prior = _feed_time(existing["timestamp"])
            if accepted < prior:
                if recent_seen is None:
                    return False
                accepted, identities = prior, set(existing["seen"])
            elif accepted == prior:
                identities.update(existing["seen"])
            if (accepted == prior and identities == set(existing["seen"])
                    and recent == existing.get("recent_seen", {})):
                return False
        self._data.setdefault("feed_cursors", {})[scope] = {
            "timestamp": accepted.isoformat().replace("+00:00", "Z"),
            "seen": sorted(identities),
            "recent_seen": recent,
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self._save()
        return True
