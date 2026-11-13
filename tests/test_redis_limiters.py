"""
Redis limiter tests against fakeredis (an in-memory Redis that also runs Lua scripts).
Two limiter objects sharing one Redis stand in for two gateway instances.
"""
import asyncio

import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.redis_limiters import RedisSlidingWindowLimiter, RedisTokenBucketLimiter
from tests.test_token_bucket import FakeClock, drain


@pytest_asyncio.fixture
async def redis():
    r = FakeAsyncRedis()
    yield r
    await r.aclose()


# ---------- token bucket ----------

@pytest.mark.asyncio
async def test_bucket_burst_then_reject(redis):
    limiter = RedisTokenBucketLimiter(redis, capacity=3, refill_per_second=1, clock=FakeClock())
    assert await drain(limiter, "a", 4) == [True, True, True, False]


@pytest.mark.asyncio
async def test_bucket_refill_and_retry_after(redis):
    clock = FakeClock()
    limiter = RedisTokenBucketLimiter(redis, capacity=2, refill_per_second=0.5, clock=clock)
    await drain(limiter, "a", 2)
    clock.now += 0.5
    decision = await limiter.check("a")
    assert not decision.allowed
    assert decision.retry_after == pytest.approx(1.5)
    clock.now += 1.5
    assert (await limiter.check("a")).allowed


@pytest.mark.asyncio
async def test_bucket_never_above_capacity(redis):
    clock = FakeClock()
    limiter = RedisTokenBucketLimiter(redis, capacity=2, refill_per_second=1, clock=clock)
    await limiter.check("a")
    clock.now += 1000
    assert await drain(limiter, "a", 3) == [True, True, False]


@pytest.mark.asyncio
async def test_bucket_shared_between_two_instances(redis):
    clock = FakeClock()
    gateway_1 = RedisTokenBucketLimiter(redis, capacity=4, refill_per_second=1, clock=clock)
    gateway_2 = RedisTokenBucketLimiter(redis, capacity=4, refill_per_second=1, clock=clock)
    assert await drain(gateway_1, "a", 2) == [True, True]
    assert await drain(gateway_2, "a", 3) == [True, True, False]


@pytest.mark.asyncio
async def test_bucket_concurrent_requests_never_exceed_capacity(redis):
    limiter = RedisTokenBucketLimiter(redis, capacity=10, refill_per_second=0.001, clock=FakeClock())
    results = await asyncio.gather(*(limiter.check("a") for _ in range(50)))
    assert sum(d.allowed for d in results) == 10


@pytest.mark.asyncio
async def test_bucket_uses_redis_server_time_without_a_clock(redis):
    limiter = RedisTokenBucketLimiter(redis, capacity=2, refill_per_second=0.001)
    assert await drain(limiter, "a", 3) == [True, True, False]


@pytest.mark.asyncio
async def test_bucket_key_expires_when_idle(redis):
    limiter = RedisTokenBucketLimiter(redis, capacity=2, refill_per_second=1, clock=FakeClock())
    await limiter.check("a")
    ttl_ms = await redis.pttl("ratelimit:tb:a")
    assert 0 < ttl_ms <= 3000  # time to refill 2 tokens + 1 s spare


# ---------- sliding window ----------

@pytest.mark.asyncio
async def test_window_allows_limit_then_rejects(redis):
    limiter = RedisSlidingWindowLimiter(redis, limit=3, window_seconds=10, clock=FakeClock())
    assert await drain(limiter, "a", 4) == [True, True, True, False]


@pytest.mark.asyncio
async def test_window_slot_opens_after_oldest_leaves(redis):
    clock = FakeClock()
    limiter = RedisSlidingWindowLimiter(redis, limit=2, window_seconds=10, clock=clock)
    await limiter.check("a")
    clock.now += 4
    await limiter.check("a")
    clock.now += 1
    decision = await limiter.check("a")
    assert not decision.allowed
    assert decision.retry_after == pytest.approx(5)
    clock.now += 5
    assert (await limiter.check("a")).allowed
    assert not (await limiter.check("a")).allowed


@pytest.mark.asyncio
async def test_window_same_instant_requests_all_count(redis):
    limiter = RedisSlidingWindowLimiter(redis, limit=3, window_seconds=10, clock=FakeClock())
    assert [(await limiter.check("a")).remaining for _ in range(3)] == [2, 1, 0]
    assert await redis.zcard("ratelimit:sw:a") == 3


@pytest.mark.asyncio
async def test_window_shared_between_two_instances(redis):
    clock = FakeClock()
    gateway_1 = RedisSlidingWindowLimiter(redis, limit=3, window_seconds=10, clock=clock)
    gateway_2 = RedisSlidingWindowLimiter(redis, limit=3, window_seconds=10, clock=clock)
    await drain(gateway_1, "a", 2)
    assert await drain(gateway_2, "a", 2) == [True, False]


@pytest.mark.asyncio
async def test_window_concurrent_requests_never_exceed_limit(redis):
    limiter = RedisSlidingWindowLimiter(redis, limit=10, window_seconds=60, clock=FakeClock())
    results = await asyncio.gather(*(limiter.check("a") for _ in range(50)))
    assert sum(d.allowed for d in results) == 10


@pytest.mark.asyncio
async def test_window_clients_are_separate(redis):
    limiter = RedisSlidingWindowLimiter(redis, limit=1, window_seconds=10, clock=FakeClock())
    await limiter.check("a")
    assert (await limiter.check("b")).allowed


# ---------- Redis down ----------

class BrokenRedis(FakeAsyncRedis):
    """Every script call fails with `error`; counts how often Redis was tried."""

    def __init__(self, error: Exception, **kwargs) -> None:
        super().__init__(**kwargs)
        self.error = error
        self.tries = 0

    async def evalsha(self, *args, **kwargs):
        self.tries += 1
        raise self.error

    async def eval(self, *args, **kwargs):
        self.tries += 1
        raise self.error


@pytest.mark.parametrize("make, full", [
    (lambda r, m: RedisTokenBucketLimiter(r, capacity=4, refill_per_second=1, monotonic=m), 4),
    (lambda r, m: RedisSlidingWindowLimiter(r, limit=6, window_seconds=10, monotonic=m), 6),
])
@pytest.mark.asyncio
async def test_redis_down_fails_open_then_skips_redis_for_a_while(make, full):
    broken = BrokenRedis(RedisConnectionError("down"))
    clock = FakeClock()
    limiter = make(broken, clock)
    decisions = [await limiter.check("a") for _ in range(3)]
    assert all(d.allowed for d in decisions)
    assert decisions[0].remaining == full  # don't tell a client to back off because our store is down
    assert broken.tries == 1  # the next two skipped Redis instead of waiting on it

    clock.now += 5  # cooldown over: try Redis again
    await limiter.check("a")
    assert broken.tries == 2


@pytest.mark.asyncio
async def test_redis_timeout_also_fails_open():
    limiter = RedisTokenBucketLimiter(BrokenRedis(RedisTimeoutError("slow")), capacity=1, refill_per_second=1)
    assert (await limiter.check("a")).allowed


@pytest.mark.asyncio
async def test_script_error_is_not_hidden():
    """A bug in the Lua script must not quietly switch rate limiting off."""
    limiter = RedisTokenBucketLimiter(BrokenRedis(ResponseError("bad script")), capacity=1, refill_per_second=1)
    with pytest.raises(ResponseError):
        await limiter.check("a")


# ---------- extreme settings / lowered limit ----------

@pytest.mark.asyncio
async def test_tiny_refill_rate_still_works(redis):
    limiter = RedisTokenBucketLimiter(redis, capacity=1, refill_per_second=1e-15, clock=FakeClock())
    assert await drain(limiter, "a", 2) == [True, False]
    assert 0 < await redis.pttl("ratelimit:tb:a") <= 86_401_000


@pytest.mark.asyncio
async def test_window_retry_after_correct_after_limit_is_lowered(redis):
    clock = FakeClock()
    old = RedisSlidingWindowLimiter(redis, limit=4, window_seconds=10, clock=clock)
    for _ in range(4):  # requests at t=0,1,2,3
        await old.check("a")
        clock.now += 1
    new = RedisSlidingWindowLimiter(redis, limit=2, window_seconds=10, clock=clock)  # t=4
    decision = await new.check("a")
    assert not decision.allowed
    assert decision.retry_after == pytest.approx(8)  # at t=12 only the t=3 request is left
