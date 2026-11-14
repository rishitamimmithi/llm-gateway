"""
The common "plug shape" every LLM provider must fit.

- ProviderResponse: what a successful call returns (same fields for every provider).
- ProviderError: what a failed call raises, labelled retryable or not.
- Provider: the abstract interface. Each provider (Groq, Gemini, Ollama) implements chat().
"""
import asyncio
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    # False when the model did not finish normally (hit the token limit, safety filter, ...):
    # the answer is still returned to the client but never cached.
    complete: bool = True


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


async def send_request(
    provider: str,
    client: httpx.AsyncClient,
    url: str,
    *,
    json: dict,
    headers: dict,
    timeout: float,
    secret: str,
) -> httpx.Response:
    """POST to a provider and turn every failure into a labelled ProviderError.

    Shared by all HTTP providers, so they all fail in the same, predictable way.
    Returns the response only if it was a success (2xx); parsing the body is the provider's job.
    """
    try:
        # httpx's own timeout applies to each phase (connect, read, ...) separately, so a slowly
        # trickling reply could run longer. asyncio.timeout adds a TOTAL deadline for the whole call.
        async with asyncio.timeout(timeout):
            resp = await client.post(url, json=json, headers=headers, timeout=timeout)
    except (httpx.TimeoutException, TimeoutError) as e:
        raise ProviderError(provider, f"timed out after {timeout}s", retryable=True) from e
    except httpx.TransportError as e:  # could not connect, connection dropped, DNS failure...
        raise ProviderError(provider, f"network error: {type(e).__name__}", retryable=True) from e
    except httpx.HTTPError as e:  # anything else httpx raises (garbled body, too many redirects...)
        raise ProviderError(provider, f"HTTP client error: {type(e).__name__}", retryable=False) from e
    except (ValueError, httpx.InvalidURL) as e:
        # The request could not even be built, e.g. a curly quote pasted into the API key in .env.
        # Only the error TYPE is reported: its text could contain part of the key.
        raise ProviderError(provider, f"could not build request: {type(e).__name__}", retryable=False) from e

    if not resp.is_success:
        raise ProviderError(
            provider,
            f"HTTP {resp.status_code}: {error_message(resp, secret)}",
            retryable=is_retryable_status(resp.status_code),
            status_code=resp.status_code,
            retry_after=retry_after_seconds(resp),
        )
    return resp


def error_message(resp: httpx.Response, secret: str) -> str:
    """Pull a short, human-readable reason out of an error response.

    Groq and Gemini both use {"error": {"message": ...}}. The provider's text is outside our
    control, so any copy of our API key (the secret) is blacked out before it reaches logs or clients.
    """
    try:
        message = str(resp.json()["error"]["message"])
    except (ValueError, KeyError, TypeError):
        message = resp.text
    if secret:  # replace("") would insert *** between every character
        message = message.replace(secret, "***")
    return message[:200]


def retry_after_seconds(resp: httpx.Response) -> float | None:
    """How long the provider asked us to wait, if it said so.

    Standard: a Retry-After header ("2"). Google may instead put it in the body:
    {"error": {"details": [{"retryDelay": "30s"}]}}.
    """
    from_header = parse_retry_after(resp.headers.get("retry-after"))
    if from_header is not None:
        return from_header
    try:
        for detail in resp.json()["error"]["details"]:
            delay = detail.get("retryDelay")
            if isinstance(delay, str) and delay.endswith("s"):
                seconds = parse_retry_after(delay[:-1])
                if seconds is not None:  # skip a broken entry and keep looking
                    return seconds
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


def to_count(value: object) -> int:
    """Turn a token count from a provider into a safe int. Anything broken ("abc", NaN, Infinity,
    None, negative) becomes 0: a good answer shouldn't be thrown away over a bad counter."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0
