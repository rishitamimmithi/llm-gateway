"""
Token bucket rate limiter (in memory, one bucket per client key).

Each bucket holds up to `capacity` tokens and refills at `refill_per_second`.
A request takes one token; with no whole token left it is rejected.
So a client can burst up to `capacity` requests at once, then gets the steady refill rate.

Refill is lazy: no timer runs, we just add (time passed x rate) whenever the bucket is used.

Async safety: check() never awaits, so on one asyncio event loop it can't be interrupted
halfway through its read-modify-write. No lock needed.
"""
import time
from collections.abc import Callable

from app.core.rate_limiter import Decision


class TokenBucketLimiter:
    def __init__(self, capacity: int, refill_per_second: float, clock: Callable[[], float] = time.monotonic) -> None:
        if capacity < 1 or refill_per_second <= 0:
            raise ValueError("capacity must be >= 1 and refill_per_second > 0")
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.clock = clock
        # key -> (tokens left, time of last update). Keys are known clients only, so this stays small.
        self._buckets: dict[str, tuple[float, float]] = {}

    async def check(self, key: str) -> Decision:
        now = self.clock()
        tokens, last = self._buckets.get(key, (float(self.capacity), now))
        elapsed = max(0.0, now - last)  # guards against a clock that steps backwards
        tokens = min(float(self.capacity), tokens + elapsed * self.refill_per_second)

        if tokens >= 1:
            tokens -= 1
            self._buckets[key] = (tokens, now)
            return Decision(True, int(tokens), 0.0)

        self._buckets[key] = (tokens, now)
        return Decision(False, 0, (1 - tokens) / self.refill_per_second)

    async def aclose(self) -> None:
        pass
