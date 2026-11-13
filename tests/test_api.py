"""
API tests for the gateway routes. They never call a real provider and never touch requests.db.
"""
import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.db.logger import RequestLog
from app.providers.base import Provider, ProviderError, ProviderResponse
from app.providers.groq import GroqProvider


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


def make_groq(handler, api_key: str | None) -> GroqProvider:
    """A real GroqProvider wired to a fake internet (no real calls)."""
    return GroqProvider(api_key, "test-model", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_chat_without_api_key_returns_502_with_clear_message(client, monkeypatch):
    monkeypatch.setattr(main, "provider", make_groq(lambda req: httpx.Response(200), api_key=None))
    resp = client.post("/chat", json={"prompt": "hi"})
    assert resp.status_code == 502
    assert "API key is missing" in resp.json()["detail"]


def test_chat_502_detail_never_contains_the_api_key(client, monkeypatch):
    key = "gsk_test_SECRET_456"
    echo_key = lambda req: httpx.Response(401, text=req.headers["authorization"])  # noqa: E731
    monkeypatch.setattr(main, "provider", make_groq(echo_key, api_key=key))

    resp = client.post("/chat", json={"prompt": "hi"})

    assert resp.status_code == 502
    assert key not in resp.text


class FakeProvider(Provider):
    """Stands in for Groq: either answers or raises the error we give it."""

    name = "fake"
    model = "fake-model"

    def __init__(self, error: ProviderError | None = None) -> None:
        self.error = error

    async def chat(self, prompt: str) -> ProviderResponse:
        if self.error:
            raise self.error
        return ProviderResponse(f"echo: {prompt}", self.name, self.model, prompt_tokens=4, completion_tokens=6)


def test_chat_success_returns_answer_and_logs_it(client, monkeypatch, no_retry_wait):
    monkeypatch.setattr(main, "provider", FakeProvider())

    resp = client.post("/chat", json={"prompt": "hi"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "echo: hi"
    assert body["usage"] == {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10}
    assert client.get("/stats").json()["successful_requests"] == 1


class FlakyProvider(FakeProvider):
    """Fails with a temporary error the first `failures` times, then answers."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures
        self.calls = 0

    async def chat(self, prompt: str) -> ProviderResponse:
        self.calls += 1
        if self.calls <= self.failures:
            raise ProviderError("fake", "HTTP 503: busy", retryable=True, status_code=503)
        return await super().chat(prompt)


@pytest.fixture
def no_retry_wait(monkeypatch):
    """Fixed retry settings, so tests are instant and don't depend on the developer's .env."""
    monkeypatch.setattr(main.config, "MAX_ATTEMPTS", 3)
    monkeypatch.setattr(main.config, "RETRY_BASE_DELAY_SECONDS", 0.0)


def test_chat_retries_a_temporary_failure_and_succeeds(client, monkeypatch, no_retry_wait):
    flaky = FlakyProvider(failures=1)
    monkeypatch.setattr(main, "provider", flaky)

    resp = client.post("/chat", json={"prompt": "hi"})

    assert resp.status_code == 200
    assert resp.json()["answer"] == "echo: hi"
    assert flaky.calls == 2  # one failure + one successful retry
    rows = main.request_log.db.execute("SELECT status FROM requests").fetchall()
    assert rows == [("ok",)]  # the client got ONE answer, so ONE row: no "error" row for the retried failure


def test_chat_gives_up_after_max_attempts(client, monkeypatch, no_retry_wait):
    flaky = FlakyProvider(failures=99)
    monkeypatch.setattr(main, "provider", flaky)

    resp = client.post("/chat", json={"prompt": "hi"})

    assert resp.status_code == 502
    assert flaky.calls == 3


def test_chat_provider_error_returns_502_and_logs_error(client, monkeypatch, no_retry_wait):
    error =ProviderError("fake", "HTTP 503: down", retryable=True, status_code=503)
    monkeypatch.setattr(main, "provider", FakeProvider(error))

    resp = client.post("/chat", json={"prompt": "hi"})

    assert resp.status_code == 502
    assert "HTTP 503" in resp.json()["detail"]
    assert client.get("/stats").json()["successful_requests"] == 0  # errors are not counted as successes
    status = main.request_log.db.execute("SELECT status FROM requests").fetchone()[0]
    assert status == "error"  # ...but they are still logged


def test_chat_rejects_missing_prompt(client):
    resp = client.post("/chat", json={})
    assert resp.status_code == 422  # FastAPI validation error: "prompt" is required
