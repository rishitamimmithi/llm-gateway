import pytest

import app.main as main
from app.core.dedup import InFlight


@pytest.fixture(autouse=True)
def no_caches_by_default(monkeypatch):
    """API tests start with the caches off, so repeated prompts really reach the fake providers.
    Cache tests switch them on explicitly."""
    monkeypatch.setattr(main, "exact_cache", None)
    monkeypatch.setattr(main, "semantic_cache", None)


@pytest.fixture(autouse=True)
def fresh_inflight(monkeypatch):
    """Every test gets its own in-flight map, so nothing leaks between tests."""
    monkeypatch.setattr(main, "inflight", InFlight())
