"""
Circuit breaker: stop calling a provider that keeps failing, then test it again after a cooldown.

States:
  CLOSED    -> normal. Count failures in a row; at `failure_threshold` -> OPEN.
  OPEN      -> reject calls instantly (no waiting on a broken provider) until `cooldown_seconds` pass.
  HALF_OPEN -> let exactly ONE trial call through. Success -> CLOSED. Failure -> OPEN again.

Async safety: allow/record methods never await, so on a single asyncio event loop each one runs
without interruption. That is why no lock is needed for "only one trial call at a time".
"""
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import TypeVar

from app.providers.base import ProviderError

T = TypeVar("T")

# Statuses that mean "this provider is unusable for us right now" even though retrying won't help:
# bad/expired key (401), not allowed (403), model gone (404).
_UNUSABLE_STATUSES = {401, 403, 404}


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(ProviderError):
    """Raised instead of calling the provider while its breaker is OPEN (or a trial is running)."""

    def __init__(self, provider: str) -> None:
        super().__init__(provider, "circuit open: provider skipped", retryable=False)


def counts_as_failure(error: ProviderError) -> bool:
    """Should this error count against the provider's health?

    Yes: temporary problems (timeouts, 5xx, 429), unusable key/model (401/403/404), and failures
    with no HTTP status at all (missing key, request could not be built).
    No: problems caused by THIS prompt (e.g. 400 bad request, prompt blocked) - the provider is fine.
    """
    if isinstance(error, CircuitOpenError):
        return False
    return error.retryable or error.status_code is None or error.status_code in _UNUSABLE_STATUSES


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int,
        cooldown_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock  # tests pass a fake clock, so they never really wait
        self._state = State.CLOSED
        self._failures = 0  # failures in a row while CLOSED
        self._opened_at = 0.0
        self._trial_running = False  # HALF_OPEN: is the one trial call in progress?
        # Bumped on every open/close. A call that started in an older generation finishes "too late":
        # its result is ignored, so it can't close an OPEN breaker or free someone else's trial slot.
        self._generation = 0

    @property
    def state(self) -> State:
        """Current state. An OPEN breaker whose cooldown has passed reports HALF_OPEN."""
        if self._state is State.OPEN and self._clock() - self._opened_at >= self.cooldown_seconds:
            return State.HALF_OPEN
        return self._state

    def allow_request(self) -> bool:
        """May we call the provider now? In HALF_OPEN this also claims the single trial slot."""
        state = self.state
        if state is State.CLOSED:
            return True
        if state is State.OPEN:
            return False
        # HALF_OPEN
        self._state = State.HALF_OPEN
        if self._trial_running:
            return False
        self._trial_running = True
        return True

    # Low-level actions for the CURRENT state. Real code should use call(), which also ignores late results.
    def record_success(self) -> None:
        self._close()

    def record_failure(self) -> None:
        if self._state is State.OPEN:
            return  # already open: a failure must not restart the cooldown
        if self._state is State.HALF_OPEN:
            self._open()  # the trial failed: back to OPEN, cooldown restarts
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open()

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Run `fn()` through the breaker and record how it went."""
        is_trial = self.state is State.HALF_OPEN
        if not self.allow_request():
            raise CircuitOpenError(self.name)
        generation = self._generation
        try:
            result = await fn()
        except ProviderError as e:
            if counts_as_failure(e):
                self._finish(generation, is_trial, self.record_failure)
            else:
                # The provider answered; this prompt was the problem. Note: this does NOT reset the
                # failure count in CLOSED - it is simply ignored (neither good nor bad news).
                self._finish(generation, is_trial, self._release_trial)
            raise
        except BaseException:
            # Cancelled, or a bug in our code: no verdict, but never keep the trial slot forever.
            self._finish(generation, is_trial, self._release_trial)
            raise
        self._finish(generation, is_trial, self.record_success if is_trial else self._reset_failures)
        return result

    def _finish(self, generation: int, is_trial: bool, action: Callable[[], None]) -> None:
        if generation != self._generation:
            return  # started before the last open/close: too late to count
        if self._state is State.HALF_OPEN and not is_trial:
            return  # only the trial's result decides HALF_OPEN
        action()

    def _release_trial(self) -> None:
        self._trial_running = False

    def _reset_failures(self) -> None:
        self._failures = 0  # a success while CLOSED: failures are no longer "in a row"

    def _open(self) -> None:
        self._state = State.OPEN
        self._opened_at = self._clock()
        self._failures = 0
        self._trial_running = False
        self._generation += 1

    def _close(self) -> None:
        self._state = State.CLOSED
        self._failures = 0
        self._trial_running = False
        self._generation += 1
