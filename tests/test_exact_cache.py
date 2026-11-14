import json

import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.cache.answer import CachedAnswer
from app.cache.exact import MemoryExactCache, RedisExactCache, cache_key, normalise_prompt
from tests.test_lru_cache import FakeClock

ANSWER = CachedAnswer("Paris", "groq", "m", 5, 2, 0.0001)


@pytest_asyncio.fixture
async def redis():
    r = FakeAsyncRedis()
    yield r
    await r.aclose()


def test_normalise_collapses_whitespace_but_keeps_case():
    assert normalise_prompt("  What is\n\tAI?  ") == "What is AI?"
    assert normalise_prompt("US") != normalise_prompt("us")


def test_key_is_a_hash_not_the_prompt():
    key = cache_key("secret question")
    assert "secret" not in key
    assert key == cache_key("secret   question ")
    assert key != cache_key("other question")


@pytest.mark.asyncio
async def test_memory_cache_hit_miss_and_whitespace():
    cache = MemoryExactCache(10, 60)
    assert await cache.get("Capital of France?") is None
    await cache.set("Capital of France?", ANSWER)
    assert await cache.get("  Capital of   France? ") == ANSWER
    assert await cache.get("capital of france?") is None


@pytest.mark.asyncio
async def test_memory_cache_expires():
    clock = FakeClock()
    cache = MemoryExactCache(10, 60, clock=clock)
    await cache.set("q", ANSWER)
    clock.now += 61
    assert await cache.get("q") is None


@pytest.mark.asyncio
async def test_redis_cache_round_trip_and_ttl(redis):
    cache = RedisExactCache(redis, ttl_seconds=60)
    assert await cache.get("q") is None
    await cache.set("q", ANSWER)
    assert await cache.get("q") == ANSWER
    ttl_ms = await redis.pttl(cache_key("q"))
    assert 0 < ttl_ms <= 60_000


@pytest.mark.asyncio
async def test_two_instances_share_redis_cache(redis):
    await RedisExactCache(redis, 60).set("q", ANSWER)
    assert await RedisExactCache(redis, 60).get("q") == ANSWER


@pytest.mark.asyncio
async def test_unpriced_answer_round_trips(redis):
    answer = CachedAnswer("hi", "gemini", "m", 1, 1, None)
    cache = RedisExactCache(redis, 60)
    await cache.set("q", answer)
    assert await cache.get("q") == answer


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b"not json", json.dumps({"text": "x"}).encode(), b"[1, 2]"])
async def test_broken_redis_entry_is_a_miss(redis, raw):
    await redis.set(cache_key("q"), raw)
    assert await RedisExactCache(redis, 60).get("q") is None


class BrokenRedis:
    async def get(self, *args, **kwargs):
        raise RedisConnectionError("down")

    async def set(self, *args, **kwargs):
        raise RedisConnectionError("down")


@pytest.mark.asyncio
async def test_redis_down_fails_open():
    cache = RedisExactCache(BrokenRedis(), 60)  # type: ignore[arg-type]
    await cache.set("q", ANSWER)  # no exception
    assert await cache.get("q") is None


class CountingBrokenRedis(BrokenRedis):
    def __init__(self):
        self.calls = 0

    async def get(self, *args, **kwargs):
        self.calls += 1
        return await super().get()

    async def set(self, *args, **kwargs):
        self.calls += 1
        return await super().set()


@pytest.mark.asyncio
async def test_redis_is_skipped_for_a_while_after_a_failure():
    clock = FakeClock()
    redis = CountingBrokenRedis()
    cache = RedisExactCache(redis, 60, down_cooldown_seconds=5, monotonic=clock)  # type: ignore[arg-type]
    assert await cache.get("q") is None
    await cache.set("q", ANSWER)
    assert await cache.get("q") is None
    assert redis.calls == 1  # only the first call waited on Redis
    clock.now += 5
    await cache.get("q")
    assert redis.calls == 2  # cooldown over: Redis tried again
