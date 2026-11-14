"""/chat with the exact and semantic caches switched on (fake providers, fake embedder)."""
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.cache.exact import MemoryExactCache
from app.cache.semantic import SemanticCache
from app.core.api_keys import ApiKeys
from app.core.token_bucket import TokenBucketLimiter
from app.db.logger import RequestLog
from app.providers.base import Provider, ProviderResponse
from tests.test_api import TEST_KEY, FakeProvider, make_router
from tests.test_semantic_cache import TABLE, FakeEmbedder


class TruncatingProvider(FakeProvider):
    async def chat(self, prompt: str) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse("cut o", self.name, self.model, 4, 6, complete=False)


class BrokenEmbedder(FakeEmbedder):
    def embed(self, texts):
        raise RuntimeError("model crashed")


@pytest.fixture
def setup(monkeypatch):
    """Returns a function that builds a client around a given provider and caches."""
    temp_log = RequestLog(":memory:")
    monkeypatch.setattr(main, "request_log", temp_log)
    monkeypatch.setattr(main, "api_keys", ApiKeys([TEST_KEY]))
    monkeypatch.setattr(main, "limiter", TokenBucketLimiter(capacity=1000, refill_per_second=1))
    monkeypatch.setattr(main.config, "MODEL_PRICES", {"fake-model": (1.0, 2.0)})  # 4*1 + 6*2 per 1M tokens

    def _setup(provider: Provider, *, exact=True, embedder=None) -> TestClient:
        monkeypatch.setattr(main, "router", make_router(provider))
        monkeypatch.setattr(main, "exact_cache", MemoryExactCache(100, 60) if exact else None)
        semantic = None
        if embedder is not None:
            semantic = SemanticCache(embedder, threshold=0.9, max_entries=100, ttl_seconds=60)
        monkeypatch.setattr(main, "semantic_cache", semantic)
        return TestClient(main.app, headers={"X-API-Key": TEST_KEY})

    yield _setup
    temp_log.close()


def chat(client: TestClient, prompt: str) -> dict:
    response = client.post("/chat", json={"prompt": prompt})
    assert response.status_code == 200
    return response.json()


def test_repeated_prompt_is_an_exact_hit(setup):
    provider = FakeProvider()
    client = setup(provider)
    first = chat(client, "capital of France?")
    second = chat(client, "  capital of   France? ")
    assert provider.calls == 1
    assert first["cache"] is None and second["cache"] == "exact"
    assert second["answer"] == first["answer"]
    assert second["cost_usd"] == 0 and second["usage"]["total_tokens"] == 0
    assert second["saved_usd"] == pytest.approx(16e-6)


def test_stats_report_hits_misses_and_money_saved(setup):
    client = setup(FakeProvider())
    for _ in range(3):
        chat(client, "capital of France?")
    chat(client, "unrelated")
    stats = client.get("/stats").json()
    assert stats["successful_requests"] == 2  # provider answers only
    assert stats["cache"] == {
        "exact_hits": 2, "semantic_hits": 0, "misses": 2, "hit_rate": 0.5, "saved_usd": pytest.approx(32e-6),
        "unpriced_hits": 0,
    }


def test_truncated_answer_is_not_cached(setup):
    provider = TruncatingProvider()
    client = setup(provider, embedder=FakeEmbedder(TABLE))
    chat(client, "capital of France?")
    assert chat(client, "capital of France?")["cache"] is None
    assert provider.calls == 2


def test_similar_prompt_is_a_semantic_hit(setup):
    provider = FakeProvider()
    client = setup(provider, embedder=FakeEmbedder(TABLE))
    first = chat(client, "capital of France?")
    second = chat(client, "What is the capital of France?")
    assert provider.calls == 1
    assert second["cache"] == "semantic"
    assert second["answer"] == first["answer"]
    assert 0.9 <= second["similarity"] < 1
    assert client.get("/stats").json()["cache"]["semantic_hits"] == 1


def test_different_prompt_misses_semantic_cache(setup):
    provider = FakeProvider()
    client = setup(provider, embedder=FakeEmbedder(TABLE))
    chat(client, "capital of France?")
    assert chat(client, "capital of Spain?")["cache"] is None
    assert provider.calls == 2


def test_exact_cache_is_checked_before_embedding(setup):
    embedder = FakeEmbedder(TABLE)
    client = setup(FakeProvider(), embedder=embedder)
    chat(client, "capital of France?")
    calls = embedder.calls
    assert chat(client, "capital of France?")["cache"] == "exact"
    assert embedder.calls == calls


def test_broken_embedder_still_answers(setup):
    provider = FakeProvider()
    client = setup(provider, exact=False, embedder=BrokenEmbedder(TABLE))
    assert chat(client, "capital of France?")["cache"] is None
    assert chat(client, "capital of France?")["cache"] is None
    assert provider.calls == 2


def test_caches_off_always_call_provider(setup):
    provider = FakeProvider()
    client = setup(provider, exact=False)
    chat(client, "q")
    chat(client, "q")
    assert provider.calls == 2


def test_build_caches_follow_config(monkeypatch):
    monkeypatch.setattr(main.config, "EXACT_CACHE_ENABLED", False)
    assert main.build_exact_cache() is None
    monkeypatch.setattr(main.config, "EXACT_CACHE_ENABLED", True)
    monkeypatch.setattr(main.config, "REDIS_URL", "")
    assert isinstance(main.build_exact_cache(), MemoryExactCache)
    monkeypatch.setattr(main.config, "SEMANTIC_CACHE_ENABLED", False)
    assert main.build_semantic_cache() is None


def test_semantic_hit_does_not_add_another_entry(setup):
    client = setup(FakeProvider(), exact=False, embedder=FakeEmbedder(TABLE))
    chat(client, "capital of France?")
    assert chat(client, "What is the capital of France?")["cache"] == "semantic"
    assert len(main.semantic_cache) == 1


def test_incomplete_answer_is_not_added_to_semantic_cache(setup):
    client = setup(TruncatingProvider(), exact=False, embedder=FakeEmbedder(TABLE))
    chat(client, "capital of France?")
    assert len(main.semantic_cache) == 0


def test_redis_down_still_answers(setup, monkeypatch):
    from app.cache.exact import RedisExactCache
    from tests.test_exact_cache import BrokenRedis

    provider = FakeProvider()
    client = setup(provider)
    monkeypatch.setattr(main, "exact_cache", RedisExactCache(BrokenRedis(), 60))  # type: ignore[arg-type]
    assert chat(client, "q")["cache"] is None
    assert chat(client, "q")["cache"] is None
    assert provider.calls == 2


def test_unpriced_hit_counts_in_stats(setup, monkeypatch):
    client = setup(FakeProvider())
    monkeypatch.setattr(main.config, "MODEL_PRICES", {})
    chat(client, "q")
    hit = chat(client, "q")
    assert hit["cache"] == "exact" and hit["saved_usd"] is None
    cache = client.get("/stats").json()["cache"]
    assert cache["exact_hits"] == 1 and cache["unpriced_hits"] == 1 and cache["saved_usd"] == 0


def test_too_long_prompt_is_rejected(setup):
    provider = FakeProvider()
    client = setup(provider)
    response = client.post("/chat", json={"prompt": "x" * (main.config.MAX_PROMPT_CHARS + 1)})
    assert response.status_code == 422
    assert provider.calls == 0
