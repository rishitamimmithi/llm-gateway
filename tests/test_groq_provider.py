"""
GroqProvider tests. httpx.MockTransport is a "fake internet": every request goes to a
handler function we write, so we can act out success, 4xx, 5xx, 429 and timeouts with no real calls.
"""
import httpx
import pytest
import pytest_asyncio

from app.providers.base import ProviderError, is_retryable_status, parse_retry_after
from app.providers.groq import GroqProvider

FAKE_KEY = "gsk_test_SECRET_123"


@pytest_asyncio.fixture
async def make_provider():
    """Builds GroqProviders wired to a fake internet, and closes them all after the test."""
    made: list[GroqProvider] = []

    def _make(handler, api_key: str | None = FAKE_KEY) -> GroqProvider:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = GroqProvider(api_key, "test-model", client=client)
        made.append(provider)
        return provider

    yield _make
    for provider in made:
        await provider.aclose()


def ok_body(text="Hello!", usage=None) -> dict:
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3} if usage is None else usage,
    }


def error_body(message: str) -> dict:
    return {"error": {"message": message}}


async def chat_error(provider: GroqProvider) -> ProviderError:
    """Call the provider, assert it raised ProviderError, and return that error."""
    with pytest.raises(ProviderError) as exc:
        await provider.chat("hi")
    return exc.value


# ---------- success ----------

@pytest.mark.asyncio
async def test_success_returns_parsed_response(make_provider):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read().decode()
        return httpx.Response(200, json=ok_body("Four"))

    result = await make_provider(handler).chat("What is 2+2?")

    assert result.text == "Four"
    assert result.provider == "groq"
    assert result.model == "test-model"
    assert (result.prompt_tokens, result.completion_tokens) == (7, 3)
    assert seen["auth"] == f"Bearer {FAKE_KEY}"
    assert '"model":"test-model"' in seen["body"].replace(" ", "")


@pytest.mark.asyncio
async def test_other_2xx_codes_count_as_success(make_provider):
    result = await make_provider(lambda req: httpx.Response(201, json=ok_body("ok"))).chat("hi")
    assert result.text == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [{}, {"prompt_tokens": None, "completion_tokens": None}])
async def test_missing_token_counts_become_zero(make_provider, usage):
    result = await make_provider(lambda req: httpx.Response(200, json=ok_body("ok", usage))).chat("hi")
    assert (result.prompt_tokens, result.completion_tokens) == (0, 0)


# ---------- retryable failures (temporary: worth trying the same provider again) ----------

@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 500, 502, 503])
async def test_temporary_statuses_are_retryable(make_provider, status):
    error = await chat_error(make_provider(lambda req: httpx.Response(status, json=error_body("busy"))))
    assert error.retryable is True
    assert error.status_code == status


@pytest.mark.asyncio
async def test_429_is_retryable_and_reads_retry_after(make_provider):
    provider = make_provider(
        lambda req: httpx.Response(429, headers={"retry-after": "2"}, json=error_body("rate limited"))
    )
    error = await chat_error(provider)
    assert error.retryable is True
    assert error.status_code == 429
    assert error.retry_after == 2.0


@pytest.mark.asyncio
async def test_timeout_is_retryable(make_provider):
    def handler(request):
        raise httpx.ReadTimeout("too slow", request=request)

    error = await chat_error(make_provider(handler))
    assert error.retryable is True
    assert error.status_code is None  # no response ever came back


@pytest.mark.asyncio
async def test_connection_error_is_retryable(make_provider):
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    assert (await chat_error(make_provider(handler))).retryable is True


# ---------- non-retryable failures (the same call would get the same "no") ----------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, message",
    [
        (400, "The model `llama-3.1-8b-instant` has been decommissioned"),
        (401, "Invalid API Key"),
        (404, "The model `llama-3.1-8b-instant` does not exist"),  # what Groq really returned
    ],
)
async def test_client_errors_are_not_retryable(make_provider, status, message):
    error = await chat_error(make_provider(lambda req: httpx.Response(status, json=error_body(message))))
    assert error.retryable is False
    assert error.status_code == status
    assert message in str(error)  # the real reason is kept, so logs explain what went wrong


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_type", [httpx.DecodingError, httpx.TooManyRedirects])
async def test_other_httpx_errors_never_escape_unlabelled(make_provider, exc_type):
    # These are neither timeouts nor transport errors; before the fix they crashed /chat with a 500.
    def handler(request):
        raise exc_type("garbled", request=request)

    error = await chat_error(make_provider(handler))
    assert error.retryable is False
    assert exc_type.__name__ in str(error)


@pytest.mark.asyncio
async def test_missing_key_fails_without_calling_groq(make_provider):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=ok_body())

    error = await chat_error(make_provider(handler, api_key=None))
    assert error.retryable is False
    assert calls == []  # no network call was made


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"unexpected": True},  # no "choices"
        {"choices": []},  # empty list
        ok_body(text=None),  # content: null (e.g. a reasoning model ran out of tokens)
        {"choices": [{"message": {"content": "hi"}}], "usage": ["not", "a", "dict"]},
    ],
)
async def test_broken_success_body_is_a_clean_error(make_provider, body):
    error = await chat_error(make_provider(lambda req: httpx.Response(200, json=body)))
    assert error.retryable is False
    assert "unexpected response format" in str(error)


# ---------- safety ----------

@pytest.mark.asyncio
@pytest.mark.parametrize("as_json", [False, True])
async def test_error_messages_never_contain_the_api_key(make_provider, as_json):
    # Even if the provider echoes our Authorization header back, it must not leak into the error.
    def handler(request):
        echoed = request.headers["authorization"]
        if as_json:
            return httpx.Response(401, json=error_body(f"bad key: {echoed}"))
        return httpx.Response(401, text=echoed)

    error = await chat_error(make_provider(handler))
    assert FAKE_KEY not in str(error)
    assert "***" in str(error)


# ---------- shared client ----------

@pytest.mark.asyncio
async def test_provider_without_injected_client_owns_and_closes_one():
    provider = GroqProvider(FAKE_KEY, "test-model")
    assert isinstance(provider._client, httpx.AsyncClient)
    await provider.aclose()
    assert provider._client.is_closed


# ---------- small helpers ----------

@pytest.mark.parametrize(
    "status, expected",
    [(200, False), (400, False), (401, False), (404, False), (408, True), (429, True), (500, True), (503, True)],
)
def test_is_retryable_status(status, expected):
    assert is_retryable_status(status) is expected


@pytest.mark.parametrize(
    "header, expected",
    [
        (None, None),
        ("3", 3.0),
        ("1.5", 1.5),
        ("-1", None),
        ("inf", None),  # would make us wait forever
        ("nan", None),
        ("Wed, 21 Oct 2026 07:28:00 GMT", None),
    ],
)
def test_parse_retry_after(header, expected):
    assert parse_retry_after(header) == expected
