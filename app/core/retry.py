"""
Retries with exponential backoff and jitter.

Busy phone line analogy: don't redial instantly forever. Wait a little, try again,
and wait longer each time (0.5 s, 1 s, 2 s, ...), up to a maximum.
Jitter adds a little randomness so many clients don't all retry at the same moment.
"""
import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.providers.base import ProviderError

T = TypeVar("T")


def backoff_delay(attempt: int, base_delay: float, max_delay: float, rand: Callable[[], float] = random.random) -> float:
    """How long to wait before retry number `attempt` (0 = the first retry).

    Exponential: base * 2^attempt, capped at max_delay.
    Jitter: a random point between 50% and 100% of that ("equal jitter").
    """
    delay = min(max_delay, base_delay * (2**attempt))
    return delay * (0.5 + rand() / 2)


async def retry_async(
    call: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_delay: float,
    max_delay: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rand: Callable[[], float] = random.random,
) -> T:
    """Run `call()`, retrying ONLY temporary ProviderErrors (retryable=True).

    - Non-retryable errors (bad key, bad model) are raised immediately: same call, same "no".
    - If the provider says how long to wait (retry_after), we use that instead of our own delay...
    - ...unless it is longer than max_delay: then we give up now, so the router can try another provider.
    - `sleep` and `rand` can be replaced in tests, so tests never really wait and are not random.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    for attempt in range(max_attempts):
        try:
            return await call()
        except ProviderError as e:
            is_last_attempt = attempt == max_attempts - 1
            if not e.retryable or is_last_attempt:
                raise
            if e.retry_after is not None:
                if e.retry_after > max_delay:
                    raise  # e.g. "come back in 24 hours": better to fall back to another provider
                delay = e.retry_after
            else:
                delay = backoff_delay(attempt, base_delay, max_delay, rand)
            await sleep(delay)

    raise AssertionError("unreachable: the loop always returns or raises")
