"""Test import bootstrap for direct pytest invocation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import pytest
import requests


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch):
    """Every test must use a fixture/fake instead of contacting a live server."""
    def blocked(*args, **kwargs):
        raise AssertionError("Live HTTP is disabled in the offline test suite")
    monkeypatch.setattr(requests.sessions.Session, "send", blocked)
