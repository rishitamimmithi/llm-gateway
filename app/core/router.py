"""
Router: try providers in order (e.g. Groq, then Gemini) until one answers.

For each provider:
  - its circuit breaker is OPEN  -> skip it instantly
  - otherwise call it with retries; any ProviderError -> move on to the next provider
Every provider attempt is reported through `on_result` (used for the request log).
If no provider answers, AllProvidersFailed lists what went wrong with each one.
"""
import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass

import anyio

from app.core.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.core.retry import retry_async
from app.providers.base import Provider, ProviderError, ProviderResponse, StreamChunk

# on_result(provider, model, prompt_tokens, completion_tokens, latency_ms, status)
ResultCallback = Callable[[str, str, int, int, float, str], None]


@dataclass
class Route:
    provider: Provider
    breaker: CircuitBreaker


class AllProvidersFailed(Exception):
    def __init__(self, errors: list[ProviderError]) -> None:
        self.errors = errors
        reasons = "; ".join(str(e) for e in errors) or "no providers configured"
        super().__init__(f"All providers failed: {reasons}")


class Router:
    def __init__(
        self,
        routes: list[Route],
        *,
        max_attempts: int,
        base_delay: float,
        max_delay: float,
        on_result: ResultCallback | None = None,
    ) -> None:
        self.routes = routes
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._on_result = on_result or (lambda *args: None)

    async def chat(self, prompt: str) -> ProviderResponse:
        errors: list[ProviderError] = []
        for route in self.routes:
            provider = route.provider
            start = time.perf_counter()
            try:
                result = await route.breaker.call(
                    lambda p=provider: retry_async(
                        lambda: p.chat(prompt),
                        max_attempts=self.max_attempts,
                        base_delay=self.base_delay,
                        max_delay=self.max_delay,
                    )
                )
            except CircuitOpenError as e:
                errors.append(e)  # skipped instantly: no call made, nothing to log
                continue
            except ProviderError as e:
                errors.append(e)
                self._on_result(provider.name, provider.model, 0, 0, _ms_since(start), "error")
                continue
            self._on_result(
                result.provider, result.model, result.prompt_tokens, result.completion_tokens, _ms_since(start), "ok"
            )
            return result
        raise AllProvidersFailed(errors)

    async def stream(self, prompt: str) -> AsyncIterator[StreamChunk]:
        """Like chat(), but yields the answer in pieces; the last piece has `final` set.

        Retries, the breaker and fallback only cover the time BEFORE the first piece: once the
        client has seen text from one provider, switching to another would mix two different
        answers. So an error after that is raised to the caller (ProviderError) instead.
        """
        errors: list[ProviderError] = []
        for route in self.routes:
            provider = route.provider
            start = time.perf_counter()
            try:
                # The breaker verdict waits for the END of the stream: a provider that always sends a
                # first piece and then dies must still count as failing.
                ticket = route.breaker.acquire()
            except CircuitOpenError as e:
                errors.append(e)
                continue
            try:
                chunks, first = await retry_async(
                    lambda p=provider: _open_stream(p, prompt),
                    max_attempts=self.max_attempts,
                    base_delay=self.base_delay,
                    max_delay=self.max_delay,
                )
            except ProviderError as e:
                route.breaker.report(ticket, e)
                errors.append(e)
                self._on_result(provider.name, provider.model, 0, 0, _ms_since(start), "error")
                continue
            except BaseException as e:
                route.breaker.report(ticket, e)
                raise

            # Committed to this provider from here on.
            finished = False
            try:
                chunk = first
                while chunk.final is None:
                    yield chunk
                    chunk = await _next_chunk(chunks, provider.name)
                r = chunk.final
                finished = True
                route.breaker.report(ticket, None)
                self._on_result(r.provider, r.model, r.prompt_tokens, r.completion_tokens, _ms_since(start), "ok")
                yield chunk
                return
            except BaseException as e:
                if not finished:
                    route.breaker.report(ticket, e)
                    # A client that went away (cancelled / closed us) isn't the provider's fault.
                    status = "cancelled" if isinstance(e, (GeneratorExit, asyncio.CancelledError)) else "error"
                    self._on_result(provider.name, provider.model, 0, 0, _ms_since(start), status)
                raise
            finally:
                # Shielded: on a disconnect the surrounding task is being cancelled, and an unshielded
                # await here would be cancelled too, leaving the provider's connection open.
                with anyio.CancelScope(shield=True):
                    await chunks.aclose()
        raise AllProvidersFailed(errors)

    def health(self) -> list[dict]:
        return [
            {"provider": r.provider.name, "model": r.provider.model, "circuit": r.breaker.state.value}
            for r in self.routes
        ]

    async def aclose(self) -> None:
        for route in self.routes:
            await route.provider.aclose()


def _ms_since(start: float) -> float:
    return (time.perf_counter() - start) * 1000


async def _next_chunk(chunks: AsyncIterator[StreamChunk], provider: str) -> StreamChunk:
    try:
        return await anext(chunks)
    except StopAsyncIteration:
        raise ProviderError(provider, "stream ended without a final piece", retryable=False) from None


async def _open_stream(provider: Provider, prompt: str) -> tuple[AsyncGenerator[StreamChunk, None], StreamChunk]:
    """Start a stream and wait for its first piece (that is the part worth retrying)."""
    chunks = provider.stream(prompt)
    try:
        return chunks, await _next_chunk(chunks, provider.name)
    except BaseException:
        await chunks.aclose()
        raise
