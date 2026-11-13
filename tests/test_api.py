"""
API tests for the gateway routes. They never call a real provider and never touch requests.db.
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.db.logger import RequestLog


@pytest.fixture
def client(monkeypatch):
    """A test client whose request log is a fresh, empty, in-memory database.

    ":memory:" means SQLite keeps the database in RAM only: fast, no files, gone after the test.
    (We avoid pytest's tmp_path here: on this machine it added 1-12 s of setup per test.)
    """
    temp_log = RequestLog(":memory:")
    monkeypatch.setattr(main, "request_log", temp_log)
    yield TestClient(main.app)
    temp_log.close()


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_stats_empty_database(client):
    resp = client.get("/stats")
    assert resp.status_code == 200
    assert resp.json() == {"successful_requests": 0, "avg_latency_ms": 0, "total_tokens": 0}


def test_stats_counts_only_successful_requests(client):
    main.request_log.log("groq", "m", 10, 20, 100.0, "ok")
    main.request_log.log("groq", "m", 5, 5, 300.0, "ok")
    main.request_log.log("groq", "m", 0, 0, 999.0, "error")  # must be ignored

    assert client.get("/stats").json() == {
        "successful_requests": 2,
        "avg_latency_ms": 200.0,  # (100 + 300) / 2 — the error's 999 ms is excluded
        "total_tokens": 40,  # 10+20 + 5+5
    }


def test_chat_without_api_key_returns_500(client, monkeypatch):
    monkeypatch.setattr(main.config, "GROQ_API_KEY", None)
    resp = client.post("/chat", json={"prompt": "hi"})
    assert resp.status_code == 500
    assert "GROQ_API_KEY" in resp.json()["detail"]


def test_chat_rejects_missing_prompt(client):
    resp = client.post("/chat", json={})
    assert resp.status_code == 422  # FastAPI validation error: "prompt" is required
