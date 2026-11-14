"""
Groq provider: calls Groq's OpenAI-compatible chat API.
Network and HTTP failures are labelled by the shared send_request(); this file only builds
the request and reads Groq's answer format.
"""
import json
from collections.abc import AsyncIterator

import httpx

from app.providers.base import (
    REQUEST_ERRORS,
    Provider,
    ProviderError,
    ProviderResponse,
    StreamChunk,
    request_error,
    send_request,
    status_error,
    to_count,
)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _parse_stream_piece(provider: str, data: str) -> tuple[str, dict, str | None]:
    """One streamed piece -> (new text, usage if this piece has it, finish_reason if set)."""
    try:
        piece = json.loads(data)
        if piece.get("error"):
            # The server broke mid-stream and said so in-band (the HTTP status was already 200)
            raise ProviderError(provider, "error in stream", retryable=True)
        choices = piece.get("choices") or [{}]
        text = (choices[0].get("delta") or {}).get("content") or ""
        if not isinstance(text, str):
            raise TypeError("content is not text")
        usage = piece.get("usage") or (piece.get("x_groq") or {}).get("usage") or {}
        return text, usage, choices[0].get("finish_reason")
    except (ValueError, IndexError, TypeError, AttributeError) as e:
        raise ProviderError(provider, "unexpected stream format", retryable=False) from e


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

    async def stream(self, prompt: str) -> AsyncIterator[StreamChunk]:
        """Same API with "stream": true: the reply is Server-Sent Events, one `data: {json}` line per
        piece, then `data: [DONE]`. Groq puts token usage in the last piece under x_groq.usage.
        The timeout here is the longest gap between pieces (httpx read timeout), not a total deadline,
        because a long answer may take far longer than one non-streamed call."""
        if not self.api_key:
            raise ProviderError(self.name, "API key is missing", retryable=False)

        body = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "stream": True}
        parts: list[str] = []
        usage: dict = {}
        finish_reason = None
        saw_done = False
        try:
            async with self._client.stream(
                "POST", self.url, json=body, headers={"Authorization": f"Bearer {self.api_key}"}, timeout=self.timeout
            ) as resp:
                if not resp.is_success:
                    await resp.aread()
                    raise status_error(self.name, resp, self.api_key)
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue  # blank separator lines, comments, other SSE fields
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        saw_done = True
                        break
                    text, chunk_usage, chunk_finish = _parse_stream_piece(self.name, data)
                    usage = chunk_usage or usage
                    finish_reason = chunk_finish or finish_reason
                    if text:
                        parts.append(text)
                        yield StreamChunk(text)
        except REQUEST_ERRORS as e:
            raise request_error(self.name, e, self.timeout) from e
        if not saw_done and finish_reason is None:
            # The connection closed without [DONE] or a finish_reason: we only have part of the answer.
            raise ProviderError(self.name, "stream ended early", retryable=True)

        yield StreamChunk(
            "",
            final=ProviderResponse(
                text="".join(parts),
                provider=self.name,
                model=self.model,
                prompt_tokens=to_count(usage.get("prompt_tokens")),
                completion_tokens=to_count(usage.get("completion_tokens")),
                # A missing finish_reason (with [DONE]) counts as complete, same rule as chat()
                complete=finish_reason in ("stop", None),
            ),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
