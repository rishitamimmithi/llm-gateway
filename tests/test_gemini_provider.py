"""
GeminiProvider tests, using httpx.MockTransport as a fake internet (no real calls).
Covers Gemini's own quirks: blocked prompts, 200-with-no-text, thinking tokens, retryDelay in the body.
"""
import json

import httpx
import pytest
import pytest_asyncio

from app.core.circuit_breaker import counts_as_failure
from app.providers.base import ProviderError
from app.providers.gemini import GeminiProvider

FAKE_KEY = "AIza_test_SECRET_789"


@pytest_asyncio.fixture
async def make_provider():
    """Builds GeminiProviders wired to a fake internet, and closes them all after the test."""
    made: list[GeminiProvider] = []

    def _make(handler, api_key: str | None = FAKE_KEY) -> GeminiProvider:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = GeminiProvider(api_key, "test-model", client=client)
        made.append(provider)
        return provider

    yield _make
    for provider in made:
        await provider.aclose()


def answer(*texts, finish="STOP", usage=None) -> dict:
    """A Gemini-style success body."""
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": t} for t in texts]}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 8} if usage is None else usage,
    }


def respond(status, body=None, **kwargs):
    return lambda request: httpx.Response(status, json=body, **kwargs)


async def chat_error(provider: GeminiProvider) -> ProviderError:
    with pytest.raises(ProviderError) as exc:
        await provider.chat("hi")
    return exc.value


# ---------- success ----------

@pytest.mark.asyncio
async def test_success_sends_correct_request_and_parses_answer(make_provider):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key_header"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.read())
        return httpx.Response(200, json=answer("Paris"))

    result = await make_provider(handler).chat("Capital of France?")

    assert result.text == "Paris"
    assert (result.provider, result.model) == ("gemini", "test-model")
    assert (result.prompt_tokens, result.completion_tokens) == (5, 8)
    assert seen["url"].endswith("/v1beta/models/test-model:generateContent")
    assert seen["key_header"] == FAKE_KEY
    assert FAKE_KEY not in seen["url"]  # the key travels in a header, never in the URL
    assert seen["body"] == {"contents": [{"role": "user", "parts": [{"text": "Capital of France?"}]}]}


@pytest.mark.asyncio
async def test_multiple_text_parts_are_joined_and_thoughts_skipped(make_provider):
    body = answer("Hello, ", "world")
    body["candidates"][0]["content"]["parts"].insert(0, {"text": "let me think...", "thought": True})
    result = await make_provider(respond(200, body)).chat("hi")
    assert result.text == "Hello, world"


@pytest.mark.asyncio
async def test_thinking_tokens_count_as_completion_tokens(make_provider):
    usage = {"promptTokenCount": 5, "candidatesTokenCount": 8, "thoughtsTokenCount": 40}
    result = await make_provider(respond(200, answer("ok", usage=usage))).chat("hi")
    assert result.completion_tokens == 48  # 8 answer + 40 thinking (both billed as output)


@pytest.mark.asyncio
async def test_missing_usage_becomes_zero(make_provider):
    result = await make_provider(respond(200, answer("ok", usage={}))).chat("hi")
    assert (result.prompt_tokens, result.completion_tokens) == (0, 0)


# ---------- 200 but no answer ----------

@pytest.mark.asyncio
async def test_blocked_prompt_is_a_clean_error(make_provider):
    body = {"promptFeedback": {"blockReason": "SAFETY"}}
    error = await chat_error(make_provider(respond(200, body)))
    assert error.retryable is False
    assert error.status_code == 200
    assert "prompt blocked: SAFETY" in str(error)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "candidate",
    [
        {"finishReason": "SAFETY"},  # no content at all
        {"content": {"parts": []}, "finishReason": "RECITATION"},  # empty parts
        {"content": {"parts": [{"inlineData": {}}]}, "finishReason": "OTHER"},  # a part with no text
    ],
)
async def test_answer_without_text_names_the_finish_reason(make_provider, candidate):
    error = await chat_error(make_provider(respond(200, {"candidates": [candidate]})))
    assert error.retryable is False
    assert f"finishReason={candidate['finishReason']}" in str(error)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"candidates": []}, {"candidates": "nope"}, ["not", "an", "object"]])
async def test_broken_success_body_is_a_clean_error(make_provider, body):
    error = await chat_error(make_provider(respond(200, body)))
    assert error.retryable is False
    assert "unexpected response format" in str(error)


# ---------- HTTP and network failures (labelled by the shared send_request) ----------

@pytest.mark.asyncio
@pytest.mark.parametrize("status, retryable", [(400, False), (401, False), (403, False), (404, False),
                                               (429, True), (500, True), (503, True), (504, True)])
async def test_http_errors_are_labelled(make_provider, status, retryable):
    body = {"error": {"code": status, "message": f"problem {status}", "status": "X"}}
    error = await chat_error(make_provider(respond(status, body)))
    assert error.retryable is retryable
    assert error.status_code == status
    assert f"problem {status}" in str(error)


@pytest.mark.asyncio
async def test_429_reads_google_retry_delay_from_body(make_provider):
    body = {"error": {"code": 429, "message": "quota", "details": [{"@type": "RetryInfo", "retryDelay": "30s"}]}}
    error = await chat_error(make_provider(respond(429, body)))
    assert error.retry_after == 30.0


@pytest.mark.asyncio
async def test_429_retry_after_header_wins_over_body(make_provider):
    body = {"error": {"code": 429, "message": "quota", "details": [{"retryDelay": "30s"}]}}
    error = await chat_error(make_provider(respond(429, body, headers={"retry-after": "4"})))
    assert error.retry_after == 4.0


@pytest.mark.asyncio
async def test_timeout_is_retryable(make_provider):
    def handler(request):
        raise httpx.ReadTimeout("too slow", request=request)

    assert (await chat_error(make_provider(handler))).retryable is True


@pytest.mark.asyncio
async def test_missing_key_fails_without_calling_gemini(make_provider):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=answer("ok"))

    error = await chat_error(make_provider(handler, api_key=None))
    assert error.retryable is False
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_count", ["abc", float("nan"), float("inf")])
async def test_broken_token_counts_do_not_throw_away_a_good_answer(make_provider, bad_count):
    usage = {"promptTokenCount": bad_count, "candidatesTokenCount": 8}
    # httpx refuses to *build* JSON with NaN/Infinity, so send it as raw text, like a real server could
    raw = json.dumps(answer("ok", usage=usage)).encode()
    handler = lambda req: httpx.Response(200, content=raw, headers={"content-type": "application/json"})  # noqa: E731
    result = await make_provider(handler).chat("hi")
    assert result.text == "ok"
    assert (result.prompt_tokens, result.completion_tokens) == (0, 8)


@pytest.mark.asyncio
@pytest.mark.parametrize("feedback", ["x", [1], {}])
async def test_odd_prompt_feedback_with_a_good_answer_still_succeeds(make_provider, feedback):
    body = answer("ok")
    body["promptFeedback"] = feedback
    result = await make_provider(respond(200, body)).chat("hi")
    assert result.text == "ok"


@pytest.mark.asyncio
async def test_only_thought_parts_means_no_text(make_provider):
    body = {"candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}]}, "finishReason": "STOP"}]}
    error = await chat_error(make_provider(respond(200, body)))
    assert "no text in answer" in str(error)
    assert error.status_code == 200


@pytest.mark.asyncio
async def test_cut_off_answer_is_returned_for_now(make_provider):
    # Known limitation (see gemini.py): MAX_TOKENS partial text still counts as success.
    result = await make_provider(respond(200, answer("The answer is", finish="MAX_TOKENS"))).chat("hi")
    assert result.text == "The answer is"


@pytest.mark.parametrize(
    "model, url_end",
    [
        ("gemini-3.1-flash-lite", "/models/gemini-3.1-flash-lite:generateContent"),
        ("models/gemini-3.1-flash-lite", "/models/gemini-3.1-flash-lite:generateContent"),  # prefix removed
        ("bad model?x=1#", "/models/bad%20model%3Fx%3D1%23:generateContent"),  # can't break the URL
    ],
)
def test_model_name_is_cleaned_for_the_url(model, url_end):
    provider = GeminiProvider("key", model, client=httpx.AsyncClient())
    assert provider.url.endswith(url_end)
    assert "?" not in provider.url and "#" not in provider.url


@pytest.mark.asyncio
async def test_bad_key_400_is_relabelled_401_so_it_counts_in_the_breaker(make_provider):
    # The real body Gemini sent for a bad key on 2026-11-14
    body = {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                      "status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}}
    error = await chat_error(make_provider(respond(400, body)))
    assert error.status_code == 401
    assert error.retryable is False
    assert "API key not valid" in str(error)
    assert counts_as_failure(error)


@pytest.mark.asyncio
async def test_other_400s_stay_400_and_do_not_count(make_provider):
    body = {"error": {"code": 400, "message": "Invalid JSON payload", "status": "INVALID_ARGUMENT"}}
    error = await chat_error(make_provider(respond(400, body)))
    assert error.status_code == 400
    assert not counts_as_failure(error)


@pytest.mark.asyncio
async def test_broken_success_body_counts_as_provider_fault(make_provider):
    error = await chat_error(make_provider(respond(200, {"candidates": []})))
    assert counts_as_failure(error)


@pytest.mark.asyncio
async def test_blocked_prompt_does_not_count_as_provider_fault(make_provider):
    error = await chat_error(make_provider(respond(200, {"promptFeedback": {"blockReason": "SAFETY"}})))
    assert not counts_as_failure(error)


@pytest.mark.asyncio
async def test_error_messages_never_contain_the_api_key(make_provider):
    def handler(request):
        echoed = request.headers["x-goog-api-key"]
        return httpx.Response(400, json={"error": {"code": 400, "message": f"API key not valid: {echoed}"}})

    error = await chat_error(make_provider(handler))
    assert FAKE_KEY not in str(error)
    assert "***" in str(error)
