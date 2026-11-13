"""
Sliding window log rate limiter (in memory, one log per client key).

Allow at most `limit` requests in ANY `window_seconds` stretch of time.
We keep the time of each allowed request and drop the ones that have left the window.

Compared with the token bucket: never a burst bigger than `limit` per window and exact counting,
but memory grows with `limit` (one timestamp per request) instead of two numbers per client.

Async safety: check() never awaits, so no lock is needed on one event loop.
"""
import time
from collections import deque
from collections.abc import Callable

from app.core.rate_limiter import Decision


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        if limit < 1 or window_seconds <= 0:
            raise ValueError("limit must be >= 1 and window_seconds > 0")
        self.limit = limit
        self.window_seconds = window_seconds
        self.clock = clock
        self._logs: dict[str, deque[float]] = {}

    async def check(self, key: str) -> Decision:
        now = self.clock()
        log = self._logs.setdefault(key, deque())
        # A request made exactly window_seconds ago has just left the window.
        while log and log[0] <= now - self.window_seconds:
            log.popleft()

        if len(log) < self.limit:
            log.append(now)
            return Decision(True, self.limit - len(log), 0.0)

        # Full: a slot opens once only limit-1 requests are left in the window. That is when the
        # oldest leaves, or a later one if the log holds more than `limit` (limit was lowered).
        return Decision(False, 0, log[len(log) - self.limit] + self.window_seconds - now)

    async def aclose(self) -> None:
        pass
