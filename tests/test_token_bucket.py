"""Token bucket tests, using a fake clock so time is exact and tests are instant."""
import asyncio

import pytest

from app.core.token_bucket import TokenBucketLimiter


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def drain(limiter, key: str, n: int) -> list[bool]:
    return [(await limiter.check(key)).allowed for _ in range(n)]


@pytest.mark.asyncio
async def test_new_client_can_burst_up_to_capacity_then_is_rejected():
    limiter = TokenBucketLimiter(capacity=5, refill_per_second=1, clock=FakeClock())
    assert await drain(limiter, "a", 6) == [True] * 5 + [False]


@pytest.mark.asyncio
async def test_remaining_counts_down():
    limiter = TokenBucketLimiter(capacity=3, refill_per_second=1, clock=FakeClock())
    assert [(await limiter.check("a")).remaining for _ in range(3)] == [2, 1, 0]


@pytest.mark.asyncio
async def test_rejection_says_when_next_token_arrives():
    clock = FakeClock()
    limiter = TokenBucketLimiter(capacity=2, refill_per_second=0.5, clock=clock)  # one token per 2 s
    await drain(limiter, "a", 2)
    clock.now += 0.5  # a quarter of a token refilled
    decision = await limiter.check("a")
    assert not decision.allowed
    assert decision.remaining == 0
    assert decision.retry_after == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_retry_after_is_accurate():
    clock = FakeClock()
    limiter = TokenBucketLimiter(capacity=1, refill_per_second=2, clock=clock)
    await limiter.check("a")
    wait = (await limiter.check("a")).retry_after
    clock.now += wait
    assert (await limiter.check("a")).allowed


@pytest.mark.asyncio
async def test_refills_over_time():
    clock = FakeClock()
    limiter = TokenBucketLimiter(capacity=5, refill_per_second=1, clock=clock)
    await drain(limiter, "a", 5)
    clock.now += 2  # two tokens back
    assert await drain(limiter, "a", 3) == [True, True, False]


@pytest.mark.asyncio
async def test_refill_never_goes_above_capacity():
    clock = FakeClock()
    limiter = TokenBucketLimiter(capacity=3, refill_per_second=1, clock=clock)
    await limiter.check("a")
    clock.now += 1000  # long idle
    assert await drain(limiter, "a", 4) == [True, True, True, False]


@pytest.mark.asyncio
async def test_rejected_requests_do_not_use_tokens():
    clock = FakeClock()
    limiter = TokenBucketLimiter(capacity=1, refill_per_second=1, clock=clock)
    await limiter.check("a")
    await drain(limiter, "a", 10)  # all rejected
    clock.now += 1
    assert (await limiter.check("a")).allowed


@pytest.mark.asyncio
async def test_clients_have_separate_buckets():
    limiter = TokenBucketLimiter(capacity=2, refill_per_second=1, clock=FakeClock())
    await drain(limiter, "a", 2)
    assert not (await limiter.check("a")).allowed
    assert (await limiter.check("b")).allowed


@pytest.mark.asyncio
async def test_clock_going_backwards_does_not_add_tokens():
    clock = FakeClock()
    limiter = TokenBucketLimiter(capacity=1, refill_per_second=1, clock=clock)
    await limiter.check("a")
    clock.now -= 50
    assert not (await limiter.check("a")).allowed


@pytest.mark.asyncio
async def test_concurrent_requests_never_exceed_capacity():
    limiter = TokenBucketLimiter(capacity=10, refill_per_second=0.001, clock=FakeClock())
    results = await asyncio.gather(*(limiter.check("a") for _ in range(100)))
    assert sum(d.allowed for d in results) == 10


@pytest.mark.parametrize("capacity, rate", [(0, 1), (5, 0), (5, -1)])
def test_bad_settings_are_refused(capacity, rate):
    with pytest.raises(ValueError):
        TokenBucketLimiter(capacity=capacity, refill_per_second=rate)
