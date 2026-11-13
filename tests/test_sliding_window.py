"""Sliding window log tests, using a fake clock."""
import asyncio

import pytest

from app.core.sliding_window import SlidingWindowLimiter
from tests.test_token_bucket import FakeClock, drain


@pytest.mark.asyncio
async def test_allows_limit_then_rejects():
    limiter = SlidingWindowLimiter(limit=3, window_seconds=10, clock=FakeClock())
    assert await drain(limiter, "a", 4) == [True, True, True, False]


@pytest.mark.asyncio
async def test_remaining_counts_down():
    limiter = SlidingWindowLimiter(limit=3, window_seconds=10, clock=FakeClock())
    assert [(await limiter.check("a")).remaining for _ in range(3)] == [2, 1, 0]


@pytest.mark.asyncio
async def test_slot_opens_when_oldest_request_leaves_window():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=2, window_seconds=10, clock=clock)
    await limiter.check("a")  # t=0
    clock.now += 4
    await limiter.check("a")  # t=4
    clock.now += 1  # t=5
    decision = await limiter.check("a")
    assert not decision.allowed
    assert decision.retry_after == pytest.approx(5)  # first request leaves at t=10

    clock.now += 4.999  # t=9.999: still inside
    assert not (await limiter.check("a")).allowed
    clock.now += 0.001  # t=10: exactly one window later, it has left
    assert (await limiter.check("a")).allowed
    assert not (await limiter.check("a")).allowed  # t=4 request still inside


@pytest.mark.asyncio
async def test_no_double_burst_across_window_edge():
    """Unlike a fixed window, a client can't do `limit` at the end of one window and `limit` at the start of the next."""
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=5, window_seconds=60, clock=clock)
    clock.now += 59
    assert await drain(limiter, "a", 5) == [True] * 5
    clock.now += 2  # a fixed window would have reset here
    assert not (await limiter.check("a")).allowed


@pytest.mark.asyncio
async def test_rejected_requests_are_not_recorded():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=1, window_seconds=10, clock=clock)
    await limiter.check("a")
    await drain(limiter, "a", 20)
    clock.now += 10
    assert (await limiter.check("a")).allowed


@pytest.mark.asyncio
async def test_clients_have_separate_windows():
    limiter = SlidingWindowLimiter(limit=1, window_seconds=10, clock=FakeClock())
    await limiter.check("a")
    assert not (await limiter.check("a")).allowed
    assert (await limiter.check("b")).allowed


@pytest.mark.asyncio
async def test_concurrent_requests_never_exceed_limit():
    limiter = SlidingWindowLimiter(limit=10, window_seconds=60, clock=FakeClock())
    results = await asyncio.gather(*(limiter.check("a") for _ in range(100)))
    assert sum(d.allowed for d in results) == 10


@pytest.mark.asyncio
async def test_retry_after_correct_when_log_holds_more_than_limit():
    """If the limit is lowered on a live log, the slot opens when entry (len - limit) leaves, not entry 0."""
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=4, window_seconds=10, clock=clock)
    for _ in range(4):  # t=0,1,2,3
        await limiter.check("a")
        clock.now += 1
    limiter.limit = 2  # t=4
    decision = await limiter.check("a")
    assert not decision.allowed
    assert decision.retry_after == pytest.approx(8)  # at t=12 only the t=3 request is left
    clock.now += 8
    assert (await limiter.check("a")).allowed


@pytest.mark.parametrize("limit, window", [(0, 10), (5, 0), (5, -1)])
def test_bad_settings_are_refused(limit, window):
    with pytest.raises(ValueError):
        SlidingWindowLimiter(limit=limit, window_seconds=window)
