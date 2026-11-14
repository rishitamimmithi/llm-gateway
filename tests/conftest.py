import pytest

import app.main as main


@pytest.fixture(autouse=True)
def no_caches_by_default(monkeypatch):
    """API tests start with the caches off, so repeated prompts really reach the fake providers.
    Cache tests switch them on explicitly."""
    monkeypatch.setattr(main, "exact_cache", None)
    monkeypatch.setattr(main, "semantic_cache", None)
