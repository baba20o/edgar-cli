"""EDGAR-aware cache layer wrapping research_cli_base FileCache.

Adds:
- Endpoint-aware TTL via URL pattern matching.
- Per-entry metadata (etag, last_modified, negative).
- Get-with-meta API for cache observability in response envelopes.
- Refresh-timestamp on 304 Not Modified responses.
- Bounded LRU eviction via `--cache-max-mb`.
- Cross-process file locks for concurrent-safe writes.
"""

from __future__ import annotations

import errno
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from research_cli_base import FileCache

DEFAULT_TTL = 900
NEGATIVE_TTL = 3600

ENDPOINT_TTLS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"/files/company_tickers_exchange\.json$"), 7 * 86400),
    (re.compile(r"/api/xbrl/companyfacts/CIK\d+\.json$"), 86400),
    (re.compile(r"/api/xbrl/companyconcept/CIK\d+/"), 86400),
    (re.compile(r"/api/xbrl/frames/"), 90 * 86400),
    (re.compile(r"/submissions/CIK\d+\.json$"), 3600),
    (re.compile(r"/submissions/CIK\d+-submissions-\d+\.json$"), 7 * 86400),
]


def ttl_for_url(url: str) -> int:
    """Return the TTL in seconds for a given SEC URL based on endpoint patterns."""
    for pattern, ttl in ENDPOINT_TTLS:
        if pattern.search(url):
            return ttl
    return DEFAULT_TTL


@contextmanager
def _file_lock(path: Path, exclusive: bool = True):
    """Best-effort cross-process advisory lock around a cache directory."""
    lock_path = path / ".lock"
    try:
        lock_path.touch(exist_ok=True)
    except OSError:
        yield
        return
    flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    fd = os.open(str(lock_path), os.O_RDWR)
    try:
        fcntl.flock(fd, flag)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class EdgarCache:
    """Wraps FileCache with conditional-header storage, per-key TTL, and metadata."""

    def __init__(self, cache_dir: str = "~/.edgar_cache", default_ttl: int = DEFAULT_TTL,
                 negative_ttl: int = NEGATIVE_TTL, max_bytes: Optional[int] = None):
        self._dir = Path(cache_dir).expanduser()
        self._dir.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl
        self.negative_ttl = negative_ttl
        self.max_bytes = max_bytes
        self._inner = FileCache(cache_dir=str(self._dir), ttl=default_ttl)

    def _key(self, url: str, params: Optional[dict] = None) -> str:
        raw = url + (json.dumps(params, sort_keys=True) if params else "")
        return hashlib.md5(raw.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def get_with_meta(self, url: str, params: Optional[dict] = None,
                      ttl: Optional[int] = None) -> tuple[Optional[Any], dict]:
        """Return (payload, meta). Payload is None on miss/stale.

        Meta always carries `key`, `hit`, plus stale_payload/etag/last_modified
        when the cached entry exists but is past its TTL — letting callers do a
        conditional GET.
        """
        ttl = ttl if ttl is not None else ttl_for_url(url)
        key = self._key(url, params)
        path = self._path(key)
        meta = {
            "key": key,
            "hit": False,
            "age_seconds": None,
            "ttl_remaining": None,
            "etag": None,
            "last_modified": None,
            "negative": False,
            "stale_payload": None,
            "stale_etag": None,
            "stale_last_modified": None,
        }
        if not path.exists():
            return None, meta
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None, meta

        ts = data.get("_ts", 0)
        age = max(0, time.time() - ts)
        etag = data.get("_etag")
        last_modified = data.get("_last_modified")
        payload = data.get("payload")
        negative = bool(data.get("_negative"))
        effective_ttl = self.negative_ttl if negative else ttl

        if age < effective_ttl:
            meta.update({
                "hit": True,
                "age_seconds": int(age),
                "ttl_remaining": int(effective_ttl - age),
                "etag": etag,
                "last_modified": last_modified,
                "negative": negative,
            })
            return payload, meta

        meta.update({
            "hit": False,
            "stale_payload": payload,
            "stale_etag": etag,
            "stale_last_modified": last_modified,
        })
        return None, meta

    def set(self, url: str, params: Optional[dict], payload: Any,
            etag: Optional[str] = None, last_modified: Optional[str] = None,
            negative: bool = False) -> None:
        """Write payload to cache with current timestamp and optional headers."""
        key = self._key(url, params)
        path = self._path(key)
        data: dict = {"_ts": time.time(), "_url": url, "payload": payload}
        if etag:
            data["_etag"] = etag
        if last_modified:
            data["_last_modified"] = last_modified
        if negative:
            data["_negative"] = True
        with _file_lock(self._dir):
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
            tmp.rename(path)
            if self.max_bytes:
                self._enforce_size_limit()

    def _enforce_size_limit(self) -> int:
        """Evict oldest entries (by mtime) until total size <= max_bytes."""
        if not self.max_bytes:
            return 0
        files = []
        total = 0
        for p in self._dir.glob("*.json"):
            try:
                stat = p.stat()
                files.append((stat.st_mtime, stat.st_size, p))
                total += stat.st_size
            except OSError:
                continue
        if total <= self.max_bytes:
            return 0
        files.sort()
        removed = 0
        for mtime, size, p in files:
            if total <= self.max_bytes:
                break
            try:
                p.unlink()
                total -= size
                removed += 1
            except OSError:
                pass
        return removed

    def invalidate(self, pattern: str) -> int:
        """Delete cached entries whose URL matches a glob pattern.

        Pattern matching uses fnmatch over the original URL stored alongside the
        payload. Wildcards: `*` (any chars), `?` (single char).
        """
        removed = 0
        with _file_lock(self._dir):
            for p in self._dir.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                url = data.get("_url", "")
                if fnmatch.fnmatch(url, pattern):
                    try:
                        p.unlink()
                        removed += 1
                    except OSError:
                        pass
        return removed

    def refresh_timestamp(self, url: str, params: Optional[dict] = None) -> bool:
        """Bump the timestamp on a cached entry without modifying its body.

        Used after a 304 Not Modified response. Returns True if refreshed.
        """
        key = self._key(url, params)
        path = self._path(key)
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        data["_ts"] = time.time()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
        tmp.rename(path)
        return True

    def clear(self) -> int:
        return self._inner.clear()

    def stats(self) -> dict:
        base = self._inner.stats()
        # The inner FileCache reports one flat ttl, but per-endpoint policies
        # override it — present it as the default plus the policy table.
        base.pop("ttl", None)
        base["default_ttl"] = self.default_ttl
        base["negative_ttl"] = self.negative_ttl
        base["max_bytes"] = self.max_bytes
        base["endpoint_ttls"] = [
            {"pattern": pattern.pattern, "ttl_seconds": ttl}
            for pattern, ttl in ENDPOINT_TTLS
        ]
        return base

    def warm(self, urls: list[str], fetcher) -> dict:
        """Pre-fetch URLs into the cache via a callable. Returns a summary.

        `fetcher(url) -> dict` is invoked only for URLs that are not already
        fresh in the cache. Useful before a long agent run to amortize SEC
        round-trips up front.
        """
        warmed = skipped = errored = 0
        for url in urls:
            payload, meta = self.get_with_meta(url)
            if meta.get("hit"):
                skipped += 1
                continue
            try:
                data = fetcher(url)
            except Exception:
                errored += 1
                continue
            if isinstance(data, dict) and "error" in data:
                self.set(url, None, data, negative=True)
            else:
                self.set(url, None, data)
            warmed += 1
        return {"warmed": warmed, "skipped_fresh": skipped, "errored": errored,
                "total": len(urls)}
