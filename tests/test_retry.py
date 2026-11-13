"""
Retry tests. A fake sleep records the waits instead of really waiting, and a fixed
"random" number makes jitter predictable, so tests are instant and never flaky.
"""
import asyncio

import httpx
import pytest

from app.core.retry import backoff_delay, retry_async
from app.providers.base import ProviderError, send_request


def temporary(retry_after: float | None = None, message: str = "HTTP 503: busy") -> ProviderError:
    return ProviderError("fake", message, retryable=True, status_code=503, retry_after=retry_after)


def permanent() -> ProviderError:
    return ProviderError("fake", "HTTP 401: bad key", retryable=False, status_code=401)


class Script:
    """A fake provider call that follows a script: raise these errors in order, then return 'ok'."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


@pytest.fixture
def waits() -> list[float]:
    return []


async def run(call, waits, *, max_attempts=3, base=0.5, max_delay=4.0, rand=lambda: 1.0):
    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    return await retry_async(
        call, max_attempts=max_attempts, base_delay=base, max_delay=max_delay, sleep=fake_sleep, rand=rand
    )


# ---------- backoff_delay (pure maths) ----------

@pytest.mark.parametrize("attempt, expected", [(0, 0.5), (1, 1.0), (2, 2.0), (3, 4.0), (4, 4.0), (10, 4.0)])
def test_delay_doubles_then_is_capped(attempt, expected):
    assert backoff_delay(attempt, base_delay=0.5, max_delay=4.0, rand=lambda: 1.0) == expected


def test_jitter_keeps_delay_between_half_and_full():
    assert backoff_delay(2, 0.5, 4.0, rand=lambda: 0.0) == 1.0  # 50% of 2.0
    assert backoff_delay(2, 0.5, 4.0, rand=lambda: 0.5) == 1.5  # 75% of 2.0
    assert backoff_delay(2, 0.5, 4.0, rand=lambda: 1.0) == 2.0  # 100% of 2.0


# ---------- retry_async ----------

@pytest.mark.asyncio
async def test_success_first_time_means_no_waiting(waits):
    call = Script()
    assert await run(call, waits) == "ok"
    assert call.calls == 1
    assert waits == []


@pytest.mark.asyncio
async def test_succeeds_after_two_temporary_failures(waits):
    call = Script(temporary(), temporary())
    assert await run(call, waits) == "ok"
    assert call.calls == 3
    assert waits == [0.5, 1.0]  # waits grow: 0.5 s, then 1 s


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts(waits):
    call = Script(*(temporary(message=f"failure #{n}") for n in (1, 2, 3, 4)))
    with pytest.raises(ProviderError) as exc:
        await run(call, waits, max_attempts=3)
    assert "failure #3" in str(exc.value)  # the LAST real error is passed on, not swallowed
    assert call.calls == 3
    assert waits == [0.5, 1.0]  # no pointless wait after the final attempt


@pytest.mark.asyncio
async def test_non_retryable_error_is_tried_exactly_once(waits):
    call = Script(permanent())
    with pytest.raises(ProviderError) as exc:
        await run(call, waits)
    assert exc.value.status_code == 401
    assert call.calls == 1
    assert waits == []


@pytest.mark.asyncio
async def test_permanent_error_after_a_temporary_one_stops_retrying(waits):
    call = Script(temporary(), permanent())
    with pytest.raises(ProviderError) as exc:
        await run(call, waits)
    assert exc.value.status_code == 401
    assert call.calls == 2


@pytest.mark.asyncio
async def test_provider_retry_after_is_respected(waits):
    call = Script(temporary(retry_after=2.0))
    assert await run(call, waits) == "ok"
    assert waits == [2.0]  # the provider's own hint, not our 0.5 s guess


@pytest.mark.asyncio
async def test_retry_after_longer_than_max_delay_gives_up_at_once(waits):
    # e.g. a 429 meaning "daily quota used up, come back in an hour": fall back instead of waiting
    call = Script(temporary(retry_after=3600.0))
    with pytest.raises(ProviderError):
        await run(call, waits, max_delay=4.0)
    assert call.calls == 1
    assert waits == []


@pytest.mark.asyncio
async def test_retry_after_exactly_max_delay_is_still_waited(waits):
    call = Script(temporary(retry_after=4.0))
    assert await run(call, waits, max_delay=4.0) == "ok"
    assert waits == [4.0]  # the limit is "longer than max_delay", so exactly 4 s is fine


@pytest.mark.asyncio
async def test_retry_after_zero_means_retry_immediately(waits):
    call = Script(temporary(retry_after=0.0))
    assert await run(call, waits) == "ok"
    assert waits == [0.0]


@pytest.mark.asyncio
async def test_cancellation_during_a_wait_is_passed_through(waits):
    # If the client disconnects while we wait, the "stop!" must not be swallowed or retried.
    call = Script(temporary(), temporary())

    async def cancelled_sleep(seconds: float) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await retry_async(call, max_attempts=3, base_delay=0.5, max_delay=4.0, sleep=cancelled_sleep)
    assert call.calls == 1  # no further attempts after the stop


@pytest.mark.asyncio
async def test_a_real_timeout_from_send_request_is_retried(waits):
    attempts = []

    async def server(request):
        attempts.append(request)
        if len(attempts) == 1:
            await asyncio.sleep(5)  # first attempt: far too slow
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
        call = lambda: send_request(  # noqa: E731
            "test", client, "https://example.com", json={}, headers={}, timeout=0.1, secret="k"
        )
        resp = await run(call, waits)

    assert resp.status_code == 200
    assert len(attempts) == 2  # timed out once, then succeeded on the retry
    assert waits == [0.5]


@pytest.mark.asyncio
async def test_max_attempts_one_means_no_retries(waits):
    call = Script(temporary())
    with pytest.raises(ProviderError):
        await run(call, waits, max_attempts=1)
    assert call.calls == 1


@pytest.mark.asyncio
async def test_unexpected_exceptions_are_not_retried(waits):
    call = Script(RuntimeError("a bug in our code"))
    with pytest.raises(RuntimeError):
        await run(call, waits)
    assert call.calls == 1  # only labelled ProviderErrors are retried, never our own bugs


@pytest.mark.asyncio
async def test_max_attempts_below_one_is_rejected(waits):
    with pytest.raises(ValueError):
        await run(Script(), waits, max_attempts=0)
