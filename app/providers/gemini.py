"""
Gemini provider: calls Google's generateContent REST API with plain httpx (no Google SDK).
Network and HTTP failures are labelled by the shared send_request(); this file builds the
request and reads Gemini's answer format, including the "200 but no answer" cases.
"""
from urllib.parse import quote

import httpx

from app.providers.base import Provider, ProviderError, ProviderResponse, send_request, to_count

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(
        self,
        api_key: str | None,
        model: str,
        *,
        base_url: str = GEMINI_BASE_URL,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model.removeprefix("models/")  # accept "models/gemini-..." as well as "gemini-..."
        # quote() stops characters like ? or # in a mistyped model name from breaking the URL
        self.url = f"{base_url}/models/{quote(self.model, safe='-._')}:generateContent"
        self.timeout = timeout
        self._client = client or httpx.AsyncClient()  # shared client: connections are reused

    async def chat(self, prompt: str) -> ProviderResponse:
        if not self.api_key:
            raise ProviderError(self.name, "API key is missing", retryable=False)

        try:
            resp = await send_request(
                self.name,
                self._client,
                self.url,
                json={"contents": [{"role": "user", "parts": [{"text": prompt}]}]},
                headers={"x-goog-api-key": self.api_key},  # header, not ?key=, so the key never sits in a URL
                timeout=self.timeout,
                secret=self.api_key,
            )
        except ProviderError as e:
            # Gemini reports a bad key as 400 ("API key not valid"), which looks like a bad prompt.
            # Re-label it 401 so it counts against the provider (circuit breaker) like any bad key.
            if e.status_code == 400 and "API key not valid" in str(e):
                raise ProviderError(self.name, str(e).split("] ", 1)[-1], retryable=False, status_code=401) from e
            raise

        try:
            data = resp.json()
            if not isinstance(data, dict):
                raise TypeError("body is not a JSON object")
        except (ValueError, TypeError) as e:
            raise self._bad_format(resp) from e

        # Gemini can answer 200 and still give no text: e.g. the prompt was blocked for safety.
        feedback = data.get("promptFeedback")
        block_reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
        if block_reason:
            raise ProviderError(self.name, f"prompt blocked: {block_reason}", retryable=False, status_code=200)

        try:
            candidate = data["candidates"][0]
            parts = (candidate.get("content") or {}).get("parts") or []
            # Join all text parts, skipping the model's internal "thought" parts if any are returned.
            text = "".join(p["text"] for p in parts if isinstance(p.get("text"), str) and not p.get("thought"))
            usage = data.get("usageMetadata") or {}
            prompt_tokens = to_count(usage.get("promptTokenCount"))
            # Thinking tokens are billed as output, so they count as completion tokens.
            completion_tokens = to_count(usage.get("candidatesTokenCount")) + to_count(
                usage.get("thoughtsTokenCount")
            )
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            raise self._bad_format(resp) from e

        if not text:
            reason = candidate.get("finishReason", "UNKNOWN")  # e.g. SAFETY, RECITATION, MAX_TOKENS
            raise ProviderError(self.name, f"no text in answer (finishReason={reason})", retryable=False, status_code=200)

        # Anything but STOP (MAX_TOKENS, SAFETY, RECITATION, ...) is returned but flagged so it is never cached.
        return ProviderResponse(
            text=text,
            provider=self.name,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            complete=candidate.get("finishReason") == "STOP",
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _bad_format(self, resp: httpx.Response) -> ProviderError:
        # status_code=None: a broken success body is the provider's fault, so it counts in the breaker
        return ProviderError(self.name, "unexpected response format", retryable=False)
