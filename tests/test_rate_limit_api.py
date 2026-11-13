"""API tests for client keys (401) and rate limits (429 + Retry-After) on /chat."""
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core.api_keys import ApiKeys
from app.core.sliding_window import SlidingWindowLimiter
from app.core.token_bucket import TokenBucketLimiter
from app.db.logger import RequestLog
from tests.test_api import FakeProvider, make_router
from tests.test_token_bucket import FakeClock

KEY_A = "client-a-key"
KEY_B = "client-b-key"


@pytest.fixture
def setup(monkeypatch):
    """Returns a function to install a limiter; gives a client with no default key."""
    temp_log = RequestLog(":memory:")
    provider = FakeProvider()
    monkeypatch.setattr(main, "request_log", temp_log)
    monkeypatch.setattr(main, "router", make_router(provider))
    monkeypatch.setattr(main, "api_keys", ApiKeys([KEY_A, KEY_B]))

    def use(limiter):
        monkeypatch.setattr(main, "limiter", limiter)

    use(TokenBucketLimiter(capacity=1000, refill_per_second=1))
    yield TestClient(main.app), use, provider
    temp_log.close()


def ask(client, key: str | None):
    headers = {} if key is None else {"X-API-Key": key}
    return client.post("/chat", json={"prompt": "hi"}, headers=headers)


# ---------- API keys ----------

def test_missing_key_is_401(setup):
    client, _, provider = setup
    r = ask(client, None)
    assert r.status_code == 401
    assert provider.calls == 0


def test_wrong_key_is_401(setup):
    client, _, provider = setup
    assert ask(client, "not-a-real-key").status_code == 401
    assert provider.calls == 0


def test_valid_key_is_answered(setup):
    client, _, _ = setup
    r = ask(client, KEY_A)
    assert r.status_code == 200
    assert "X-RateLimit-Remaining" in r.headers


def test_no_keys_configured_rejects_everyone(setup, monkeypatch):
    client, _, _ = setup
    monkeypatch.setattr(main, "api_keys", ApiKeys([]))
    assert ask(client, KEY_A).status_code == 401


def test_raw_key_is_not_the_client_id():
    client_id = ApiKeys([KEY_A]).client_id(KEY_A)
    assert client_id and KEY_A not in client_id


def test_other_endpoints_need_no_key(setup):
    client, _, _ = setup
    assert client.get("/health").status_code == 200
    assert client.get("/stats").status_code == 200


# ---------- rate limits ----------

def test_over_limit_is_429_with_retry_after(setup):
    client, use, provider = setup
    clock = FakeClock()
    use(TokenBucketLimiter(capacity=2, refill_per_second=0.25, clock=clock))  # one token per 4 s

    first, second = ask(client, KEY_A), ask(client, KEY_A)
    assert [first.status_code, second.status_code] == [200, 200]
    assert [first.headers["X-RateLimit-Remaining"], second.headers["X-RateLimit-Remaining"]] == ["1", "0"]

    third = ask(client, KEY_A)
    assert third.status_code == 429
    assert third.headers["Retry-After"] == "4"
    assert third.headers["X-RateLimit-Remaining"] == "0"
    assert provider.calls == 2  # the limited request never reached a provider

    clock.now += 4
    assert ask(client, KEY_A).status_code == 200


def test_retry_after_is_rounded_up_and_at_least_1(setup):
    client, use, _ = setup
    clock = FakeClock()
    use(TokenBucketLimiter(capacity=1, refill_per_second=10, clock=clock))  # next token in 0.1 s
    ask(client, KEY_A)
    assert ask(client, KEY_A).headers["Retry-After"] == "1"


def test_limits_are_per_key(setup):
    client, use, _ = setup
    use(TokenBucketLimiter(capacity=1, refill_per_second=0.01, clock=FakeClock()))
    assert ask(client, KEY_A).status_code == 200
    assert ask(client, KEY_A).status_code == 429
    assert ask(client, KEY_B).status_code == 200


def test_invalid_keys_do_not_use_up_a_limit(setup):
    client, use, _ = setup
    use(TokenBucketLimiter(capacity=1, refill_per_second=0.01, clock=FakeClock()))
    for _ in range(5):
        ask(client, "bad")
    assert ask(client, KEY_A).status_code == 200


def test_sliding_window_limiter_works_behind_the_api(setup):
    client, use, _ = setup
    clock = FakeClock()
    use(SlidingWindowLimiter(limit=2, window_seconds=30, clock=clock))
    assert [ask(client, KEY_A).status_code for _ in range(3)] == [200, 200, 429]
    clock.now += 10
    r = ask(client, KEY_A)
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "20"


def test_build_limiter_picks_algorithm_and_store(monkeypatch):
    monkeypatch.setattr(main.config, "REDIS_URL", "")
    monkeypatch.setattr(main.config, "RATE_LIMIT_ALGORITHM", "sliding_window")
    assert isinstance(main.build_limiter(), SlidingWindowLimiter)
    monkeypatch.setattr(main.config, "RATE_LIMIT_ALGORITHM", "token_bucket")
    assert isinstance(main.build_limiter(), TokenBucketLimiter)

    from app.core.redis_limiters import RedisSlidingWindowLimiter, RedisTokenBucketLimiter

    monkeypatch.setattr(main.config, "REDIS_URL", "redis://localhost:6379/0")  # no connection is made yet
    assert isinstance(main.build_limiter(), RedisTokenBucketLimiter)
    monkeypatch.setattr(main.config, "RATE_LIMIT_ALGORITHM", "sliding_window")
    assert isinstance(main.build_limiter(), RedisSlidingWindowLimiter)
