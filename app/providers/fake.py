"""
Fake provider for load tests: waits a fixed time, then returns a made-up answer.

With PROVIDER_ORDER=fake, a load test measures the gateway itself (cache, dedup, rate limits,
logging) instead of a free tier's rate limits, and it costs nothing. Never use it in production.
"""
import asyncio
from collections.abc import AsyncIterator

from app.providers.base import Provider, ProviderResponse, StreamChunk

FAKE_COMPLETION_TOKENS = 60


class FakeProvider(Provider):
    name = "fake"

    def __init__(self, model: str = "fake-llm", *, delay_seconds: float = 0.3) -> None:
        self.model = model
        self.delay_seconds = delay_seconds

    async def chat(self, prompt: str) -> ProviderResponse:
        await asyncio.sleep(self.delay_seconds)  # stands in for network + model time
        return self._answer(prompt)

    async def stream(self, prompt: str) -> AsyncIterator[StreamChunk]:
        """Word by word, spreading the same total delay over the words."""
        answer = self._answer(prompt)
        words = answer.text.split(" ")
        for i, word in enumerate(words):
            await asyncio.sleep(self.delay_seconds / len(words))
            yield StreamChunk(word if i == 0 else " " + word)
        yield StreamChunk("", final=answer)

    def _answer(self, prompt: str) -> ProviderResponse:
        return ProviderResponse(
            text=f"Fake answer to: {prompt[:80]}",
            provider=self.name,
            model=self.model,
            prompt_tokens=max(1, len(prompt) // 4),  # roughly 4 characters per token
            completion_tokens=FAKE_COMPLETION_TOKENS,
        )
