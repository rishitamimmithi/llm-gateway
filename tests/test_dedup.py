"""In-flight request deduplication (app/core/dedup.py)."""
import asyncio

import pytest

from app.core.dedup import InFlight


class SlowWork:
    """Counts calls and waits until the test releases it, so requests really overlap."""

    def __init__(self, result="answer", error: Exception | None = None) -> None:
        self.calls = 0
        self.release = asyncio.Event()
        self.result = result
        self.error = error

    async def __call__(self):
        self.calls += 1
        await self.release.wait()
        if self.error:
            raise self.error
        return self.result


async def settle() -> None:
    """Let every started task run up to its first wait."""
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_fifty_identical_requests_make_one_call():
    inflight, work = InFlight(), SlowWork()
    tasks = [asyncio.create_task(inflight.run("k", work)) for _ in range(50)]
    await settle()
    work.release.set()
    results = await asyncio.gather(*tasks)
    assert work.calls == 1
    assert all(result == "answer" for result, _ in results)
    assert sum(not shared for _, shared in results) == 1  # exactly one leader
    assert (inflight.leaders, inflight.followers) == (1, 49)


@pytest.mark.asyncio
async def test_different_keys_do_not_share():
    inflight, work = InFlight(), SlowWork()
    tasks = [asyncio.create_task(inflight.run(key, work)) for key in ("a", "b", "a")]
    await settle()
    assert len(inflight) == 2
    work.release.set()
    await asyncio.gather(*tasks)
    assert work.calls == 2


@pytest.mark.asyncio
async def test_error_is_shared_by_everyone_waiting():
    inflight, work = InFlight(), SlowWork(error=RuntimeError("provider down"))
    tasks = [asyncio.create_task(inflight.run("k", work)) for _ in range(3)]
    await settle()
    work.release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert work.calls == 1
    assert all(isinstance(r, RuntimeError) for r in results)


@pytest.mark.asyncio
async def test_key_is_forgotten_after_finishing_so_next_request_calls_again():
    inflight, work = InFlight(), SlowWork()
    work.release.set()
    await inflight.run("k", work)
    await settle()
    assert len(inflight) == 0
    await inflight.run("k", work)
    assert work.calls == 2


@pytest.mark.asyncio
async def test_key_is_forgotten_after_an_error_too():
    inflight, work = InFlight(), SlowWork(error=ValueError("bad"))
    work.release.set()
    with pytest.raises(ValueError):
        await inflight.run("k", work)
    await settle()
    assert len(inflight) == 0


@pytest.mark.asyncio
async def test_leader_cancelled_does_not_cancel_work_for_followers():
    inflight, work = InFlight(), SlowWork()
    leader = asyncio.create_task(inflight.run("k", work))
    follower = asyncio.create_task(inflight.run("k", work))
    await settle()
    leader.cancel()  # e.g. the first client hung up
    await settle()
    work.release.set()
    assert await follower == ("answer", True)
    assert leader.cancelled()
    assert work.calls == 1


@pytest.mark.asyncio
async def test_everyone_cancelled_and_work_fails_gives_no_unretrieved_error_warning():
    inflight, work = InFlight(), SlowWork(error=RuntimeError("boom"))
    waiter = asyncio.create_task(inflight.run("k", work))
    await settle()
    waiter.cancel()
    work.release.set()
    await settle()
    assert len(inflight) == 0  # the done callback ran (and read the exception)


@pytest.mark.asyncio
async def test_aclose_waits_for_calls_still_in_flight():
    inflight, work = InFlight(), SlowWork()
    waiter = asyncio.create_task(inflight.run("k", work))
    await settle()
    closing = asyncio.create_task(inflight.aclose())
    await settle()
    assert not closing.done()
    work.release.set()
    await closing
    assert await waiter == ("answer", False)
