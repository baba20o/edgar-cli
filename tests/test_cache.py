"""Cache layer: portable locking and atomic overwrites (Windows + POSIX)."""

import threading

from edgar import cache as cache_mod
from edgar.cache import EdgarCache

URL = "https://data.sec.gov/submissions/CIK0000320193.json"


def test_set_overwrites_an_existing_entry(tmp_path):
    # Path.rename() raises FileExistsError on Windows when the target exists;
    # the second write of the same key is the case that used to break there.
    cache = EdgarCache(cache_dir=str(tmp_path))

    cache.set(URL, None, {"v": 1})
    cache.set(URL, None, {"v": 2}, etag='"abc"')

    payload, meta = cache.get_with_meta(URL)
    assert payload == {"v": 2}
    assert meta["hit"] is True
    assert meta["etag"] == '"abc"'
    assert not list(tmp_path.glob("*.tmp"))


def test_refresh_timestamp_rewrites_in_place(tmp_path):
    cache = EdgarCache(cache_dir=str(tmp_path))
    cache.set(URL, None, {"v": 1})

    assert cache.refresh_timestamp(URL) is True
    assert cache.refresh_timestamp("https://data.sec.gov/missing.json") is False

    payload, _ = cache.get_with_meta(URL)
    assert payload == {"v": 1}


def test_invalidate_and_size_limit_work_under_the_lock(tmp_path):
    cache = EdgarCache(cache_dir=str(tmp_path), max_bytes=10_000_000)
    cache.set(URL, None, {"v": 1})
    cache.set("https://data.sec.gov/api/xbrl/frames/x.json", None, {"v": 2})

    assert cache.invalidate("*CIK0000320193*") == 1
    assert cache.get_with_meta(URL)[0] is None


def test_cache_works_when_no_lock_primitive_is_available(tmp_path, monkeypatch):
    # Neither fcntl (POSIX) nor msvcrt (Windows): locking degrades to a no-op
    # instead of failing at import or at write time.
    monkeypatch.setattr(cache_mod, "fcntl", None)
    monkeypatch.setattr(cache_mod, "msvcrt", None)
    cache = EdgarCache(cache_dir=str(tmp_path))

    cache.set(URL, None, {"v": 1})

    assert cache.get_with_meta(URL)[0] == {"v": 1}


def test_file_lock_serializes_writers(tmp_path):
    # Two threads take the directory lock through separate descriptors; the
    # critical sections must not interleave on either platform.
    events: list[str] = []
    first_inside = threading.Event()
    release_first = threading.Event()

    def first():
        with cache_mod._file_lock(tmp_path):
            events.append("first-in")
            first_inside.set()
            release_first.wait(timeout=5)
            events.append("first-out")

    def second():
        first_inside.wait(timeout=5)
        with cache_mod._file_lock(tmp_path):
            events.append("second-in")

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    t2.start()
    first_inside.wait(timeout=5)
    # Give the second thread time to block on the lock, then let go.
    t2.join(timeout=0.3)
    release_first.set()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert events == ["first-in", "first-out", "second-in"]


def test_file_lock_times_out_instead_of_hanging(tmp_path, monkeypatch):
    # Best-effort contract: if the lock cannot be taken, the body still runs.
    monkeypatch.setattr(cache_mod, "_acquire", lambda fd, exclusive: False)
    ran = []

    with cache_mod._file_lock(tmp_path):
        ran.append(True)

    assert ran == [True]
