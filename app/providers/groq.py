"""
Groq provider: calls Groq's OpenAI-compatible chat API.
Network and HTTP failures are labelled by the shared send_request(); this file only builds
the request and reads Groq's answer format.
"""
import httpx

from app.providers.base import Provider, ProviderError, ProviderResponse, send_request, to_count

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

        resp = await send_request(
            self.name,
            self._client,
            self.url,
            json={"model": self.model, "messages": [{"role": "user", "content": prompt}]},
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
            secret=self.api_key,
        )

        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            if not isinstance(text, str):
                raise TypeError("content is not text")  # e.g. null when the model returns no answer
            usage = data.get("usage") or {}
            prompt_tokens = to_count(usage.get("prompt_tokens"))
            completion_tokens = to_count(usage.get("completion_tokens"))
            # Only a normal "stop" counts as complete: "length" = cut off, "content_filter" = filtered.
            # A missing finish_reason is treated as complete (some compatible APIs leave it out).
            complete = data["choices"][0].get("finish_reason") in ("stop", None)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            # A success code with a broken body is unexpected; retrying the same call is unlikely to fix it.
            # status_code=None: it's the provider's fault, so it counts in the circuit breaker.
            raise ProviderError(self.name, "unexpected response format", retryable=False) from e

        return ProviderResponse(
            text=text,
            provider=self.name,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            complete=complete,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
