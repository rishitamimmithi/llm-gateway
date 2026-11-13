"""
Circuit breaker tests: one test per state transition / rule. A fake clock means no real waiting.
"""
import asyncio

import pytest

from app.core.circuit_breaker import CircuitBreaker, CircuitOpenError, State, counts_as_failure
from app.providers.base import ProviderError

THRESHOLD = 3
COOLDOWN = 30.0


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def breaker(clock) -> CircuitBreaker:
    return CircuitBreaker("fake", failure_threshold=THRESHOLD, cooldown_seconds=COOLDOWN, clock=clock)


def fail(breaker: CircuitBreaker, times: int) -> None:
    for _ in range(times):
        assert breaker.allow_request()
        breaker.record_failure()


def open_breaker(breaker: CircuitBreaker) -> None:
    fail(breaker, THRESHOLD)
    assert breaker.state is State.OPEN


def temporary() -> ProviderError:
    return ProviderError("fake", "HTTP 503: busy", retryable=True, status_code=503)


def bad_prompt() -> ProviderError:
    return ProviderError("fake", "HTTP 400: bad request", retryable=False, status_code=400)


async def ok() -> str:
    return "ok"


# ---------- the 8 required rules ----------

def test_1_closed_stays_closed_below_threshold(breaker):
    fail(breaker, THRESHOLD - 1)
    assert breaker.state is State.CLOSED
    assert breaker.allow_request()


def test_2_closed_opens_at_threshold(breaker):
    fail(breaker, THRESHOLD)
    assert breaker.state is State.OPEN


def test_3_success_in_closed_resets_the_failure_count(breaker):
    fail(breaker, THRESHOLD - 1)
    breaker.record_success()
    fail(breaker, THRESHOLD - 1)  # would have opened if the count had not been reset
    assert breaker.state is State.CLOSED


@pytest.mark.asyncio
async def test_4_open_rejects_instantly_before_cooldown(breaker, clock):
    open_breaker(breaker)
    clock.advance(COOLDOWN - 0.1)
    calls = []

    async def provider_call():
        calls.append(1)
        return "ok"

    with pytest.raises(CircuitOpenError):
        await breaker.call(provider_call)
    assert calls == []  # the provider was never called


def test_5_open_becomes_half_open_after_cooldown(breaker, clock):
    open_breaker(breaker)
    clock.advance(COOLDOWN)
    assert breaker.state is State.HALF_OPEN


@pytest.mark.asyncio
async def test_6_half_open_trial_success_closes(breaker, clock):
    open_breaker(breaker)
    clock.advance(COOLDOWN)
    assert await breaker.call(ok) == "ok"
    assert breaker.state is State.CLOSED
    fail(breaker, THRESHOLD - 1)  # failure count started fresh
    assert breaker.state is State.CLOSED


@pytest.mark.asyncio
async def test_7_half_open_trial_failure_reopens_and_restarts_cooldown(breaker, clock):
    open_breaker(breaker)
    clock.advance(COOLDOWN)

    async def broken():
        raise temporary()

    with pytest.raises(ProviderError):
        await breaker.call(broken)
    assert breaker.state is State.OPEN
    clock.advance(COOLDOWN - 0.1)
    assert breaker.state is State.OPEN  # the cooldown started again from the failed trial
    clock.advance(0.1)
    assert breaker.state is State.HALF_OPEN


@pytest.mark.asyncio
async def test_8_half_open_lets_only_one_trial_through_at_a_time(breaker, clock):
    open_breaker(breaker)
    clock.advance(COOLDOWN)
    gate = asyncio.Event()
    calls = []

    async def slow_trial():
        calls.append(1)
        await gate.wait()
        return "ok"

    trial = asyncio.create_task(breaker.call(slow_trial))
    await asyncio.sleep(0)  # let the trial start and claim the slot

    for _ in range(5):  # 5 more requests arrive while the trial is still running
        with pytest.raises(CircuitOpenError):
            await breaker.call(ok)

    gate.set()
    assert await trial == "ok"
    assert calls == [1]  # exactly one call reached the provider
    assert breaker.state is State.CLOSED


# ---------- extra rules ----------

@pytest.mark.asyncio
async def test_cancelled_trial_frees_the_slot(breaker, clock):
    open_breaker(breaker)
    clock.advance(COOLDOWN)

    async def hangs():
        await asyncio.sleep(10)

    trial = asyncio.create_task(breaker.call(hangs))
    await asyncio.sleep(0)
    trial.cancel()
    with pytest.raises(asyncio.CancelledError):
        await trial

    assert await breaker.call(ok) == "ok"  # not stuck: a new trial is allowed
    assert breaker.state is State.CLOSED


@pytest.mark.asyncio
async def test_prompt_caused_errors_do_not_count(breaker):
    async def rejected_prompt():
        raise bad_prompt()

    for _ in range(THRESHOLD + 2):
        with pytest.raises(ProviderError):
            await breaker.call(rejected_prompt)
    assert breaker.state is State.CLOSED


@pytest.mark.asyncio
async def test_prompt_caused_error_during_trial_keeps_half_open(breaker, clock):
    open_breaker(breaker)
    clock.advance(COOLDOWN)

    async def rejected_prompt():
        raise bad_prompt()

    with pytest.raises(ProviderError):
        await breaker.call(rejected_prompt)
    assert breaker.state is State.HALF_OPEN  # no verdict yet...
    assert await breaker.call(ok) == "ok"  # ...so the next request may be the trial
    assert breaker.state is State.CLOSED


@pytest.mark.asyncio
async def test_call_records_counted_failures_and_opens(breaker):
    calls = []

    async def broken():
        calls.append(1)
        raise temporary()

    for _ in range(THRESHOLD):
        with pytest.raises(ProviderError, match="503"):  # the real error, not CircuitOpenError
            await breaker.call(broken)
    assert calls == [1] * THRESHOLD
    assert breaker.state is State.OPEN


def test_failure_while_open_does_not_restart_cooldown(breaker, clock):
    open_breaker(breaker)
    clock.advance(COOLDOWN - 1)
    for _ in range(THRESHOLD):
        breaker.record_failure()
    clock.advance(1)
    assert breaker.state is State.HALF_OPEN


@pytest.mark.asyncio
async def test_prompt_errors_are_ignored_not_a_reset(breaker):
    # Documented choice: a 400 is neither good nor bad news, so 503, 400, 503, 503 still opens.
    for error in (temporary(), bad_prompt(), temporary(), temporary()):
        async def fails(error=error):
            raise error

        with pytest.raises(ProviderError):
            await breaker.call(fails)
    assert breaker.state is State.OPEN


# ---------- late results: calls that started before the breaker opened ----------

async def start_slow_calls(breaker: CircuitBreaker, n: int):
    """Start n calls while CLOSED that wait on a gate; returns (gate, tasks, outcome setter)."""
    gate = asyncio.Event()
    outcome: dict = {"raise": None}

    async def slow():
        await gate.wait()
        if outcome["raise"] is not None:
            raise outcome["raise"]
        return "late ok"

    tasks = [asyncio.create_task(breaker.call(slow)) for _ in range(n)]
    await asyncio.sleep(0)  # let them all start (breaker still CLOSED)
    return gate, tasks, outcome


@pytest.mark.asyncio
async def test_late_success_cannot_close_an_open_breaker(breaker):
    gate, tasks, _ = await start_slow_calls(breaker, 1)
    open_breaker(breaker)  # meanwhile the provider dies
    gate.set()
    assert await tasks[0] == "late ok"  # the caller still gets its answer...
    assert breaker.state is State.OPEN  # ...but the old success does not close the breaker


@pytest.mark.asyncio
async def test_late_failures_do_not_restart_cooldown(breaker, clock):
    gate, tasks, outcome = await start_slow_calls(breaker, THRESHOLD)
    open_breaker(breaker)
    clock.advance(COOLDOWN - 1)
    outcome["raise"] = temporary()
    gate.set()
    for task in tasks:
        with pytest.raises(ProviderError, match="503"):
            await task
    clock.advance(1)
    assert breaker.state is State.HALF_OPEN  # cooldown counted from the original opening


@pytest.mark.asyncio
@pytest.mark.parametrize("late_ending", ["cancel", "bad_prompt", "success", "failure"])
async def test_late_calls_cannot_touch_a_running_trial(breaker, clock, late_ending):
    gate, tasks, outcome = await start_slow_calls(breaker, 1)
    open_breaker(breaker)
    clock.advance(COOLDOWN)

    trial_gate = asyncio.Event()
    trial_calls = []

    async def trial():
        trial_calls.append(1)
        await trial_gate.wait()
        raise temporary()  # the trial FAILS

    trial_task = asyncio.create_task(breaker.call(trial))
    await asyncio.sleep(0)

    # The old call finishes in some way while the trial is running.
    if late_ending == "cancel":
        tasks[0].cancel()
    elif late_ending == "bad_prompt":
        outcome["raise"] = bad_prompt()
    elif late_ending == "failure":
        outcome["raise"] = temporary()
    gate.set()
    await asyncio.gather(tasks[0], return_exceptions=True)

    with pytest.raises(CircuitOpenError):  # the trial slot is still taken
        await breaker.call(ok)
    assert breaker.state is State.HALF_OPEN

    trial_gate.set()
    with pytest.raises(ProviderError, match="503"):
        await trial_task
    assert trial_calls == [1]
    assert breaker.state is State.OPEN  # the failed trial decides, nothing else


@pytest.mark.asyncio
async def test_bugs_in_our_code_are_passed_on_and_not_counted(breaker):
    async def buggy():
        raise RuntimeError("bug")

    for _ in range(THRESHOLD):
        with pytest.raises(RuntimeError):
            await breaker.call(buggy)
    assert breaker.state is State.CLOSED


def test_circuit_open_error_is_a_non_retryable_provider_error():
    error = CircuitOpenError("groq")
    assert isinstance(error, ProviderError)
    assert error.retryable is False  # so retry_async never waits on an open breaker
    assert "circuit open" in str(error)


def test_threshold_below_one_is_rejected(clock):
    with pytest.raises(ValueError):
        CircuitBreaker("x", failure_threshold=0, cooldown_seconds=1, clock=clock)


@pytest.mark.parametrize(
    "error, counts",
    [
        (ProviderError("p", "timeout", retryable=True), True),
        (ProviderError("p", "503", retryable=True, status_code=503), True),
        (ProviderError("p", "429", retryable=True, status_code=429), True),
        (ProviderError("p", "401", retryable=False, status_code=401), True),  # bad key: provider unusable
        (ProviderError("p", "403", retryable=False, status_code=403), True),
        (ProviderError("p", "404", retryable=False, status_code=404), True),  # model retired
        (ProviderError("p", "missing key", retryable=False), True),  # no status: never reached provider
        (ProviderError("p", "400", retryable=False, status_code=400), False),  # bad prompt
        (ProviderError("p", "blocked", retryable=False, status_code=200), False),  # prompt blocked
        (CircuitOpenError("p"), False),
    ],
)
def test_counts_as_failure(error, counts):
    assert counts_as_failure(error) is counts
