"""
The common "plug shape" every LLM provider must fit.

- ProviderResponse: what a successful call returns (same fields for every provider).
- ProviderError: what a failed call raises, labelled retryable or not.
- Provider: the abstract interface. Each provider (Groq, Gemini, Ollama) implements chat().
"""
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int


class ProviderError(Exception):
    """A provider call failed.

    `retryable` answers ONE question: "is it worth calling THIS SAME provider again?"
      True  -> temporary problem (timeout, 5xx, 429): trying again later may work.
      False -> our request is wrong (bad key, bad model, other 4xx): the same call gets the same "no".
    It does NOT decide fallback: the router (Step 6) moves to the next provider on ANY ProviderError.
    """

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.retryable = retryable
        self.status_code = status_code  # HTTP status from the provider, None if no response came back
        self.retry_after = retry_after  # seconds the provider asked us to wait (from a 429), if any


class Provider(ABC):
    """Every provider must have a name, a model, and an async chat() method."""

    name: str
    model: str

    @abstractmethod
    async def chat(self, prompt: str) -> ProviderResponse:
        """Send one prompt and return the answer, or raise ProviderError."""

    async def aclose(self) -> None:
        """Release network connections when the gateway shuts down. Default: nothing to release."""


def is_retryable_status(status_code: int) -> bool:
    """429 (too many requests), 408 (request timeout) and 5xx (server broke) are worth retrying."""
    return status_code in (408, 429) or status_code >= 500


def parse_retry_after(value: str | None) -> float | None:
    """Read a Retry-After header given in seconds (e.g. "2" or "1.5"). Anything else -> None."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None  # HTTP-date format is allowed by the spec but rare from LLM APIs; ignore it
    # isfinite rejects "inf" and "nan": waiting forever would hang the request
    return seconds if math.isfinite(seconds) and seconds >= 0 else None
