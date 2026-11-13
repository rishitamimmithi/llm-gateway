"""
Groq provider: calls Groq's OpenAI-compatible chat API and turns every failure
into a labelled ProviderError (retryable or not).
"""
import httpx

from app.providers.base import (
    Provider,
    ProviderError,
    ProviderResponse,
    is_retryable_status,
    parse_retry_after,
)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(Provider):
    name = "groq"

    def __init__(
        self,
        api_key: str | None,
        model: str,
        *,
        url: str = GROQ_URL,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.url = url
        self.timeout = timeout
        # One shared client = connections are reused (no new TCP + TLS handshake per request).
        # Tests pass their own client with a fake transport.
        self._client = client or httpx.AsyncClient()

    async def chat(self, prompt: str) -> ProviderResponse:
        if not self.api_key:
            raise ProviderError(self.name, "API key is missing", retryable=False)

        payload = {"model": self.model, "messages": [{"role": "user", "content": prompt}]}
        headers = {"Authorization": f"Bearer {self.api_key}"}

        try:
            # Note: httpx applies this timeout to each phase (connect, read, ...), not the whole call.
            resp = await self._client.post(self.url, json=payload, headers=headers, timeout=self.timeout)
        except httpx.TimeoutException as e:
            raise ProviderError(self.name, f"timed out after {self.timeout}s", retryable=True) from e
        except httpx.TransportError as e:  # could not connect, connection dropped, DNS failure...
            raise ProviderError(self.name, f"network error: {type(e).__name__}", retryable=True) from e
        except httpx.HTTPError as e:  # anything else httpx raises (garbled body, too many redirects...)
            raise ProviderError(self.name, f"HTTP client error: {type(e).__name__}", retryable=False) from e

        if not resp.is_success:
            raise ProviderError(
                self.name,
                f"HTTP {resp.status_code}: {_error_message(resp, self.api_key)}",
                retryable=is_retryable_status(resp.status_code),
                status_code=resp.status_code,
                retry_after=parse_retry_after(resp.headers.get("retry-after")),
            )

        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            if not isinstance(text, str):
                raise TypeError("content is not text")  # e.g. null when the model returns no answer
            usage = data.get("usage") or {}
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            # A success code with a broken body is unexpected; retrying the same call is unlikely to fix it.
            raise ProviderError(
                self.name, "unexpected response format", retryable=False, status_code=resp.status_code
            ) from e

        return ProviderResponse(
            text=text,
            provider=self.name,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _error_message(resp: httpx.Response, api_key: str) -> str:
    """Pull a short, human-readable reason out of an error response.

    The provider's text is outside our control, so any copy of our API key is blacked out
    before the message can reach logs or the client.
    """
    try:
        message = str(resp.json()["error"]["message"])
    except (ValueError, KeyError, TypeError):
        message = resp.text
    return message.replace(api_key, "***")[:200]
