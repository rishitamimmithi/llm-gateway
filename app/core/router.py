"""
Router: try providers in order (e.g. Groq, then Gemini) until one answers.

For each provider:
  - its circuit breaker is OPEN  -> skip it instantly
  - otherwise call it with retries; any ProviderError -> move on to the next provider
Every provider attempt is reported through `on_result` (used for the request log).
If no provider answers, AllProvidersFailed lists what went wrong with each one.
"""
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.core.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.core.retry import retry_async
from app.providers.base import Provider, ProviderError, ProviderResponse

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
