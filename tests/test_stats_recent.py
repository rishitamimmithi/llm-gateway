"""GET /stats/recent: newest requests first, with a short prompt preview and how each was served."""
import app.main as main
from app.cache.exact import MemoryExactCache
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_streaming import PiecesProvider
from tests.test_api import make_router


def test_recent_lists_newest_first_with_preview_and_how_it_was_served(client, monkeypatch):
    monkeypatch.setattr(main, "exact_cache", MemoryExactCache(100, 60))
    client.post("/chat", json={"prompt": "what is   an LRU cache"})
    client.post("/chat", json={"prompt": "what is   an LRU cache"})
    rows = client.get("/stats/recent").json()
    assert [r["status"] for r in rows] == ["cache_exact", "ok"]
    assert all(r["prompt_preview"] == "what is an LRU cache" for r in rows)
    assert rows[1]["provider"] == "fake" and rows[1]["latency_ms"] is not None


def test_preview_is_cut_and_can_be_switched_off(client, monkeypatch):
    monkeypatch.setattr(main.config, "PROMPT_PREVIEW_CHARS", 10)
    client.post("/chat", json={"prompt": "a" * 50})
    monkeypatch.setattr(main.config, "PROMPT_PREVIEW_CHARS", 0)
    client.post("/chat", json={"prompt": "secret"})
    rows = client.get("/stats/recent").json()
    assert rows[0]["prompt_preview"] is None
    assert rows[1]["prompt_preview"] == "a" * 10 + "…"


def test_streamed_requests_get_a_preview_too(client, monkeypatch):
    monkeypatch.setattr(main, "router", make_router(PiecesProvider()))
    client.post("/chat/stream", json={"prompt": "stream me"})
    assert client.get("/stats/recent").json()[0]["prompt_preview"] == "stream me"


def test_limit_is_checked(client):
    client.post("/chat", json={"prompt": "one"})
    client.post("/chat", json={"prompt": "two"})
    assert len(client.get("/stats/recent?limit=1").json()) == 1
    assert client.get("/stats/recent?limit=0").status_code == 422
    assert client.get("/stats/recent?limit=501").status_code == 422
