"""
Router unit tests with fake providers: order, fallback, skipping open breakers, logging callback.
"""
import pytest

from app.core.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.core.router import AllProvidersFailed, Route, Router
from app.providers.base import Provider, ProviderError, ProviderResponse


class Fake(Provider):
    def __init__(self, name: str, error: ProviderError | None = None) -> None:
        self.name = name
        self.model = f"{name}-m"
        self.error = error
        self.calls = 0
        self.closed = False

    async def chat(self, prompt: str) -> ProviderResponse:
        self.calls += 1
        if self.error:
            raise self.error
        return ProviderResponse("ok", self.name, self.model, 1, 2)

    async def aclose(self) -> None:
        self.closed = True


def router_for(*providers: Provider, rows: list | None = None) -> Router:
    routes = [Route(p, CircuitBreaker(p.name, failure_threshold=2, cooldown_seconds=30)) for p in providers]
    on_result = (lambda *row: rows.append(row)) if rows is not None else None
    return Router(routes, max_attempts=3, base_delay=0.0, max_delay=4.0, on_result=on_result)


@pytest.mark.asyncio
async def test_first_healthy_provider_answers_and_later_ones_are_not_called():
    a, b = Fake("a"), Fake("b")
    result = await router_for(a, b).chat("hi")
    assert result.provider == "a"
    assert (a.calls, b.calls) == (1, 0)


@pytest.mark.asyncio
async def test_non_retryable_error_is_tried_once_then_falls_back():
    a = Fake("a", ProviderError("a", "HTTP 401: bad key", retryable=False, status_code=401))
    b = Fake("b")
    result = await router_for(a, b).chat("hi")
    assert result.provider == "b"
    assert a.calls == 1  # no pointless retries on a bad key


@pytest.mark.asyncio
async def test_open_breaker_is_skipped_without_calling_the_provider():
    a = Fake("a", ProviderError("a", "HTTP 503", retryable=True, status_code=503))
    b = Fake("b")
    router = router_for(a, b)
    await router.chat("1")
    await router.chat("2")  # threshold 2: a's breaker is now open
    calls_before = a.calls
    assert (await router.chat("3")).provider == "b"
    assert a.calls == calls_before


@pytest.mark.asyncio
async def test_all_failing_lists_every_error_including_skips():
    a = Fake("a", ProviderError("a", "HTTP 503", retryable=True, status_code=503))
    router = router_for(a)
    for _ in range(2):
        with pytest.raises(AllProvidersFailed):
            await router.chat("x")
    with pytest.raises(AllProvidersFailed) as exc:
        await router.chat("x")
    assert isinstance(exc.value.errors[0], CircuitOpenError)
    assert "circuit open" in str(exc.value)


@pytest.mark.asyncio
async def test_no_providers_configured():
    with pytest.raises(AllProvidersFailed, match="no providers configured"):
        await router_for().chat("x")


@pytest.mark.asyncio
async def test_each_attempted_provider_is_reported_once():
    rows: list = []
    a = Fake("a", ProviderError("a", "HTTP 503", retryable=True, status_code=503))
    await router_for(a, Fake("b"), rows=rows).chat("hi")
    assert [(r[0], r[5]) for r in rows] == [("a", "error"), ("b", "ok")]
    assert rows[1][2:4] == (1, 2)  # tokens of the successful answer


@pytest.mark.asyncio
async def test_skipped_provider_is_not_reported():
    rows: list = []
    a = Fake("a", ProviderError("a", "HTTP 503", retryable=True, status_code=503))
    router = router_for(a, Fake("b"), rows=rows)
    await router.chat("1")
    await router.chat("2")
    rows.clear()
    await router.chat("3")  # a is open: skipped, no row for it
    assert [r[0] for r in rows] == ["b"]


@pytest.mark.asyncio
async def test_aclose_closes_every_provider():
    a, b = Fake("a"), Fake("b")
    await router_for(a, b).aclose()
    assert a.closed and b.closed
