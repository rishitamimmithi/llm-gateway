"""/chat with many identical requests arriving at the same time."""
import asyncio

import httpx
import pytest

import app.main as main
from app.cache.exact import MemoryExactCache
from app.core.api_keys import ApiKeys
from app.core.dedup import InFlight
from app.core.token_bucket import TokenBucketLimiter
from app.db.logger import RequestLog
from app.providers.base import ProviderResponse
from tests.test_api import TEST_KEY, FakeProvider, make_router, temporary


class GatedProvider(FakeProvider):
    """Waits until the test opens the gate, so all requests are in flight together."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def chat(self, prompt: str) -> ProviderResponse:
        self.started.set()
        await self.gate.wait()
        return await super().chat(prompt)


@pytest.fixture
def gateway(monkeypatch):
    temp_log = RequestLog(":memory:")
    monkeypatch.setattr(main, "request_log", temp_log)
    monkeypatch.setattr(main, "api_keys", ApiKeys([TEST_KEY]))
    monkeypatch.setattr(main, "limiter", TokenBucketLimiter(capacity=1000, refill_per_second=1))
    monkeypatch.setattr(main.config, "MODEL_PRICES", {"fake-model": (1.0, 2.0)})  # 16e-6 per answer

    def _setup(provider, *, exact_cache=None, dedup=True):
        monkeypatch.setattr(main, "router", make_router(provider))
        monkeypatch.setattr(main, "exact_cache", exact_cache)
        monkeypatch.setattr(main, "inflight", InFlight() if dedup else None)
        transport = httpx.ASGITransport(app=main.app)
        return httpx.AsyncClient(transport=transport, base_url="http://gw", headers={"X-API-Key": TEST_KEY})

    yield _setup
    temp_log.close()


async def burst(client: httpx.AsyncClient, provider: GatedProvider, prompts: list[str]) -> list[httpx.Response]:
    tasks = [asyncio.create_task(client.post("/chat", json={"prompt": p})) for p in prompts]
    await provider.started.wait()
    for _ in range(20):  # let the other requests reach the in-flight map
        await asyncio.sleep(0)
    provider.gate.set()
    return await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_fifty_identical_requests_make_one_provider_call(gateway):
    provider = GatedProvider()
    async with gateway(provider) as client:
        responses = await burst(client, provider, ["capital of France?"] * 50)
        stats = (await client.get("/stats")).json()
    bodies = [r.json() for r in responses]
    assert all(r.status_code == 200 for r in responses)
    assert provider.calls == 1
    assert len({b["answer"] for b in bodies}) == 1
    leaders = [b for b in bodies if not b["deduplicated"]]
    followers = [b for b in bodies if b["deduplicated"]]
    assert len(leaders) == 1 and len(followers) == 49
    assert leaders[0]["cost_usd"] == pytest.approx(16e-6)
    assert all(f["cost_usd"] == 0 and f["saved_usd"] == pytest.approx(16e-6) for f in followers)
    assert stats["successful_requests"] == 1
    assert stats["cache"]["deduplicated"] == 49
    assert stats["cache"]["saved_usd"] == pytest.approx(49 * 16e-6)
    assert len(main.inflight) == 0


@pytest.mark.asyncio
async def test_extra_spaces_still_count_as_the_same_request(gateway):
    provider = GatedProvider()
    async with gateway(provider) as client:
        await burst(client, provider, ["capital of France?", "  capital of   France? "])
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_different_prompts_are_not_merged(gateway):
    provider = GatedProvider()
    async with gateway(provider) as client:
        responses = await burst(client, provider, ["a?", "b?", "a?"])
    assert provider.calls == 2
    assert [r.json()["answer"] for r in responses] == ["fake: a?", "fake: b?", "fake: a?"]


@pytest.mark.asyncio
async def test_all_providers_failing_gives_every_waiter_503_with_one_call_chain(gateway):
    provider = GatedProvider(error=temporary("fake"))
    async with gateway(provider) as client:
        responses = await burst(client, provider, ["q"] * 10)
    assert [r.status_code for r in responses] == [503] * 10
    assert provider.calls == 3  # one request's retries, not 10 × 3


@pytest.mark.asyncio
async def test_with_dedup_off_every_request_calls_the_provider(gateway):
    provider = GatedProvider()
    async with gateway(provider, dedup=False) as client:
        await burst(client, provider, ["q"] * 5)
    assert provider.calls == 5


@pytest.mark.asyncio
async def test_after_the_shared_call_the_next_request_is_a_cache_hit(gateway):
    provider = GatedProvider()
    async with gateway(provider, exact_cache=MemoryExactCache(100, 60)) as client:
        await burst(client, provider, ["q"] * 5)
        later = (await client.post("/chat", json={"prompt": "q"})).json()
    assert provider.calls == 1
    assert later["cache"] == "exact"


class BrokenCache(MemoryExactCache):
    async def set(self, prompt, answer):
        raise RuntimeError("cache bug")


@pytest.mark.asyncio
async def test_cache_failure_after_a_paid_answer_does_not_fail_the_burst(gateway):
    provider = GatedProvider()
    async with gateway(provider, exact_cache=BrokenCache(100, 60)) as client:
        responses = await burst(client, provider, ["q"] * 5)
    assert [r.status_code for r in responses] == [200] * 5
    assert provider.calls == 1


def test_cache_node_name_has_no_password():
    assert main.cache_node_name("redis://:s3cret@cache1:6380/2") == "cache1:6380/2"
    assert main.cache_node_name("redis://cache2") == "cache2:6379/0"
