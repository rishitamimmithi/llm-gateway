"""
API tests for the gateway routes. They never call a real provider and never touch requests.db.
"""
import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core.api_keys import ApiKeys
from app.core.circuit_breaker import CircuitBreaker
from app.core.token_bucket import TokenBucketLimiter
from app.core.router import Route, Router
from app.db.logger import RequestLog
from app.providers.base import Provider, ProviderError, ProviderResponse
from app.providers.groq import GroqProvider


class FakeProvider(Provider):
    """Answers, or fails the first `failures` times with `error` (forever if failures is None)."""

    def __init__(self, name: str = "fake", error: ProviderError | None = None, failures: int | None = None) -> None:
        self.name = name
        self.model = f"{name}-model"
        self.error = error
        self.failures = failures
        self.calls = 0

    async def chat(self, prompt: str) -> ProviderResponse:
        self.calls += 1
        if self.error and (self.failures is None or self.calls <= self.failures):
            raise self.error
        return ProviderResponse(f"{self.name}: {prompt}", self.name, self.model, prompt_tokens=4, completion_tokens=6)


TEST_KEY = "test-client-key"


def temporary(name: str) -> ProviderError:
    return ProviderError(name, "HTTP 503: down", retryable=True, status_code=503)


def make_router(*providers: Provider, threshold: int = 3) -> Router:
    routes = [Route(p, CircuitBreaker(p.name, failure_threshold=threshold, cooldown_seconds=30)) for p in providers]
    return Router(
        routes, max_attempts=3, base_delay=0.0, max_delay=4.0, on_result=lambda *row: main.record_result(*row)
    )


@pytest.fixture
def client(monkeypatch):
    """A test client with a fresh in-memory request log and a router with one answering provider."""
    temp_log = RequestLog(":memory:")
    monkeypatch.setattr(main, "request_log", temp_log)
    monkeypatch.setattr(main, "router", make_router(FakeProvider()))
    monkeypatch.setattr(main, "api_keys", ApiKeys([TEST_KEY]))
    monkeypatch.setattr(main, "limiter", TokenBucketLimiter(capacity=1000, refill_per_second=1))  # never in the way
    yield TestClient(main.app, headers={"X-API-Key": TEST_KEY})
    temp_log.close()


def logged_rows() -> list[tuple]:
    return main.request_log.db.execute("SELECT provider, status FROM requests ORDER BY id").fetchall()


# ---------- basic routes ----------

def test_health_returns_ok(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_stats_empty_database(client):
    assert client.get("/stats").json() == {
        "successful_requests": 0,
        "avg_latency_ms": 0,
        "total_tokens": 0,
        "total_cost_usd": 0,
        "unpriced_requests": 0,
        "by_provider": {},
    }


def test_stats_counts_only_successful_requests(client):
    main.request_log.log("groq", "m", 10, 20, 100.0, "ok")
    main.request_log.log("groq", "m", 5, 5, 300.0, "ok")
    main.request_log.log("groq", "m", 0, 0, 999.0, "error")  # must be ignored
    body = client.get("/stats").json()
    assert (body["successful_requests"], body["avg_latency_ms"], body["total_tokens"]) == (2, 200.0, 40)


def test_stats_adds_up_cost_per_provider_and_counts_unpriced(client):
    main.request_log.log("groq", "m", 10, 20, 100.0, "ok", 0.002)
    main.request_log.log("groq", "m", 5, 5, 100.0, "ok", 0.001)
    main.request_log.log("gemini", "g", 1, 1, 100.0, "ok", None)  # unknown price
    main.request_log.log("gemini", "g", 0, 0, 100.0, "error", 0.0)  # failures never count
    body = client.get("/stats").json()
    assert body["total_cost_usd"] == 0.003
    assert body["unpriced_requests"] == 1
    assert body["by_provider"] == {
        "gemini": {"successful_requests": 1, "total_tokens": 2, "total_cost_usd": 0, "unpriced_requests": 1},
        "groq": {"successful_requests": 2, "total_tokens": 40, "total_cost_usd": 0.003, "unpriced_requests": 0},
    }


def test_chat_returns_and_logs_cost_from_the_price_table(client, monkeypatch):
    # fake-model: 4 prompt tokens at $1/1M + 6 completion tokens at $2/1M = $0.000016
    monkeypatch.setattr(main.config, "MODEL_PRICES", {"fake-model": (1.0, 2.0)})
    body = client.post("/chat", json={"prompt": "hi"}).json()
    assert body["cost_usd"] == pytest.approx(0.000016)
    logged = main.request_log.db.execute("SELECT cost_usd FROM requests").fetchone()[0]
    assert logged == pytest.approx(0.000016)


def test_chat_cost_is_none_for_a_model_without_a_price(client, monkeypatch):
    monkeypatch.setattr(main.config, "MODEL_PRICES", {})
    assert client.post("/chat", json={"prompt": "hi"}).json()["cost_usd"] is None
    assert client.get("/stats").json()["unpriced_requests"] == 1


def test_chat_rejects_missing_prompt(client):
    assert client.post("/chat", json={}).status_code == 422


# ---------- /chat through the router ----------

def test_chat_success_returns_answer_provider_and_logs_it(client):
    resp = client.post("/chat", json={"prompt": "hi"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "fake: hi"
    assert (body["provider"], body["model"]) == ("fake", "fake-model")
    assert body["usage"] == {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10}
    assert logged_rows() == [("fake", "ok")]


def test_chat_falls_back_when_first_provider_fails(client, monkeypatch):
    groq = FakeProvider("groq", error=temporary("groq"))
    gemini = FakeProvider("gemini")
    monkeypatch.setattr(main, "router", make_router(groq, gemini))

    resp = client.post("/chat", json={"prompt": "hi"})

    assert resp.status_code == 200
    assert resp.json()["provider"] == "gemini"
    assert groq.calls == 3  # retried, then gave up on groq
    assert logged_rows() == [("groq", "error"), ("gemini", "ok")]
    assert client.get("/stats").json()["successful_requests"] == 1


def test_chat_returns_503_when_all_providers_fail(client, monkeypatch):
    monkeypatch.setattr(
        main, "router", make_router(FakeProvider("groq", temporary("groq")), FakeProvider("gemini", temporary("gemini")))
    )
    resp = client.post("/chat", json={"prompt": "hi"})
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "[groq] HTTP 503" in detail and "[gemini] HTTP 503" in detail
    assert logged_rows() == [("groq", "error"), ("gemini", "error")]


def test_chat_retries_a_temporary_failure_on_the_same_provider(client, monkeypatch):
    flaky = FakeProvider("groq", error=temporary("groq"), failures=1)
    monkeypatch.setattr(main, "router", make_router(flaky, FakeProvider("gemini")))

    resp = client.post("/chat", json={"prompt": "hi"})

    assert resp.json()["provider"] == "groq"
    assert flaky.calls == 2
    assert logged_rows() == [("groq", "ok")]  # one answer, one row


def test_wrong_groq_key_falls_back_with_zero_failed_requests(client, monkeypatch):
    # The Phase 2 "done" scenario: Groq has a wrong key, every client request still succeeds.
    bad_key = lambda req: httpx.Response(401, json={"error": {"message": "Invalid API Key"}})  # noqa: E731
    groq = GroqProvider("gsk_wrong", "m", client=httpx.AsyncClient(transport=httpx.MockTransport(bad_key)))
    monkeypatch.setattr(main, "router", make_router(groq, FakeProvider("gemini"), threshold=3))

    statuses = [client.post("/chat", json={"prompt": f"q{i}"}).status_code for i in range(10)]

    assert statuses == [200] * 10
    health = {p["provider"]: p["circuit"] for p in client.get("/health/providers").json()}
    assert health == {"groq": "open", "gemini": "closed"}
    groq_errors = [row for row in logged_rows() if row == ("groq", "error")]
    assert len(groq_errors) == 3  # after 3 failures the breaker skips groq instantly


def test_503_detail_never_contains_the_api_key(client, monkeypatch):
    key = "gsk_test_SECRET_456"
    echo_key = lambda req: httpx.Response(401, text=req.headers["authorization"])  # noqa: E731
    groq = GroqProvider(key, "m", client=httpx.AsyncClient(transport=httpx.MockTransport(echo_key)))
    monkeypatch.setattr(main, "router", make_router(groq))

    resp = client.post("/chat", json={"prompt": "hi"})

    assert resp.status_code == 503
    assert key not in resp.text


def test_health_providers_lists_each_breaker(client, monkeypatch):
    monkeypatch.setattr(main, "router", make_router(FakeProvider("groq"), FakeProvider("gemini")))
    assert client.get("/health/providers").json() == [
        {"provider": "groq", "model": "groq-model", "circuit": "closed"},
        {"provider": "gemini", "model": "gemini-model", "circuit": "closed"},
    ]


# ---------- build_router (from config) ----------

def test_build_router_skips_providers_without_a_key(monkeypatch):
    monkeypatch.setattr(main.config, "PROVIDER_ORDER", ["groq", "gemini"])
    monkeypatch.setattr(main.config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(main.config, "GEMINI_API_KEY", None)
    assert [r.provider.name for r in main.build_router().routes] == ["groq"]


def test_build_router_follows_provider_order(monkeypatch):
    monkeypatch.setattr(main.config, "PROVIDER_ORDER", ["gemini", "groq"])
    monkeypatch.setattr(main.config, "GROQ_API_KEY", "k1")
    monkeypatch.setattr(main.config, "GEMINI_API_KEY", "k2")
    assert [r.provider.name for r in main.build_router().routes] == ["gemini", "groq"]


def test_build_router_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(main.config, "PROVIDER_ORDER", ["groq", "openai"])
    with pytest.raises(ValueError, match="Unknown provider"):
        main.build_router()
