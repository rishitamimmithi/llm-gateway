"""
Streaming (Server-Sent Events): providers yield pieces, the router falls back only before the first
piece, and POST /chat/stream relays pieces as `chunk` events and ends with `done` or `error`.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.cache.exact import MemoryExactCache
from app.core.api_keys import ApiKeys
from app.core.router import AllProvidersFailed
from app.core.token_bucket import TokenBucketLimiter
from app.db.logger import RequestLog
from app.providers.base import ProviderError, ProviderResponse, StreamChunk
from app.providers.fake import FakeProvider as LoadTestProvider
from app.providers.groq import GroqProvider
from tests.test_api import TEST_KEY, FakeProvider, make_router, temporary

FAKE_KEY = "gsk_test_SECRET_123"


class PiecesProvider(FakeProvider):
    """Streams `pieces`, then either a final chunk or `mid_error` (raised after the pieces)."""

    def __init__(self, name="fake", pieces=("Hel", "lo"), mid_error=None, complete=True, **kw) -> None:
        super().__init__(name, **kw)
        self.pieces = pieces
        self.mid_error = mid_error
        self.complete = complete
        self.closed = False

    async def stream(self, prompt):
        self.calls += 1
        if self.error and (self.failures is None or self.calls <= self.failures):
            raise self.error
        try:
            for piece in self.pieces:
                yield StreamChunk(piece)
            if self.mid_error:
                raise self.mid_error
            yield StreamChunk(
                "", final=ProviderResponse("".join(self.pieces), self.name, self.model, 4, 6, complete=self.complete)
            )
        finally:
            self.closed = True


async def collect(chunks) -> list[StreamChunk]:
    return [chunk async for chunk in chunks]


# ---------- providers ----------


@pytest.mark.asyncio
async def test_default_stream_sends_the_whole_answer_then_final():
    chunks = await collect(FakeProvider().stream("hi"))
    assert [c.text for c in chunks] == ["fake: hi", ""]
    assert chunks[-1].final.text == "fake: hi"


@pytest.mark.asyncio
async def test_load_test_provider_streams_word_by_word():
    provider = LoadTestProvider(delay_seconds=0)
    chunks = await collect(provider.stream("hi there"))
    final = chunks[-1].final
    assert len(chunks) > 3
    assert "".join(c.text for c in chunks) == final.text == (await provider.chat("hi there")).text


def sse_body(*pieces: dict, done: bool = True) -> bytes:
    lines = [f"data: {json.dumps(p)}\n\n" for p in pieces]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def groq(handler) -> GroqProvider:
    return GroqProvider(FAKE_KEY, "test-model", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_groq_stream_reads_pieces_usage_and_finish_reason():
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        body = sse_body(
            {"choices": [{"delta": {"role": "assistant", "content": ""}}]},
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}],
             "x_groq": {"usage": {"prompt_tokens": 7, "completion_tokens": 2}}},
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    provider = groq(handler)
    chunks = await collect(provider.stream("hi"))
    await provider.aclose()
    assert sent["stream"] is True
    assert [c.text for c in chunks] == ["Hel", "lo", ""]
    final = chunks[-1].final
    assert (final.text, final.prompt_tokens, final.completion_tokens, final.complete) == ("Hello", 7, 2, True)


@pytest.mark.asyncio
async def test_groq_stream_cut_off_answer_is_not_complete():
    def handler(request):
        return httpx.Response(200, content=sse_body({"choices": [{"delta": {"content": "a"}, "finish_reason": "length"}]}))

    provider = groq(handler)
    chunks = await collect(provider.stream("hi"))
    await provider.aclose()
    assert chunks[-1].final.complete is False


@pytest.mark.asyncio
async def test_groq_stream_error_status_is_labelled_and_key_hidden():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": f"bad key {FAKE_KEY}"}})

    provider = groq(handler)
    with pytest.raises(ProviderError) as info:
        await collect(provider.stream("hi"))
    await provider.aclose()
    assert info.value.status_code == 401 and not info.value.retryable
    assert FAKE_KEY not in str(info.value)


@pytest.mark.asyncio
async def test_groq_stream_broken_piece_raises_provider_error():
    def handler(request):
        return httpx.Response(200, content=b"data: {not json\n\n")

    provider = groq(handler)
    with pytest.raises(ProviderError, match="unexpected stream format"):
        await collect(provider.stream("hi"))
    await provider.aclose()


@pytest.mark.asyncio
async def test_groq_stream_error_piece_raises_instead_of_a_partial_answer():
    def handler(request):
        body = sse_body({"choices": [{"delta": {"content": "Hel"}}]}, {"error": {"message": "overloaded"}})
        return httpx.Response(200, content=body)

    provider = groq(handler)
    with pytest.raises(ProviderError, match="error in stream"):
        await collect(provider.stream("hi"))
    await provider.aclose()


@pytest.mark.asyncio
async def test_groq_stream_cut_connection_is_not_a_complete_answer():
    def handler(request):
        return httpx.Response(200, content=sse_body({"choices": [{"delta": {"content": "Hel"}}]}, done=False))

    provider = groq(handler)
    with pytest.raises(ProviderError, match="ended early"):
        await collect(provider.stream("hi"))
    await provider.aclose()


@pytest.mark.asyncio
async def test_groq_stream_network_error_is_retryable():
    def handler(request):
        raise httpx.ConnectError("refused")

    provider = groq(handler)
    with pytest.raises(ProviderError) as info:
        await collect(provider.stream("hi"))
    await provider.aclose()
    assert info.value.retryable


# ---------- router ----------


@pytest.fixture
def rows(monkeypatch):
    log: list[tuple] = []
    monkeypatch.setattr(main, "record_result", lambda *row: log.append((row[0], row[-1])))
    return log


@pytest.mark.asyncio
async def test_router_stream_falls_back_before_the_first_piece(rows):
    broken = PiecesProvider("groq", error=temporary("groq"))
    router = make_router(broken, PiecesProvider("gemini"))
    chunks = await collect(router.stream("hi"))
    assert chunks[-1].final.provider == "gemini"
    assert broken.calls == 3  # retried, then fell back
    assert rows == [("groq", "error"), ("gemini", "ok")]


@pytest.mark.asyncio
async def test_router_stream_does_not_fall_back_after_the_first_piece(rows):
    first = PiecesProvider("groq", mid_error=temporary("groq"))
    backup = PiecesProvider("gemini")
    router = make_router(first, backup)
    received = []
    with pytest.raises(ProviderError):
        async for chunk in router.stream("hi"):
            received.append(chunk.text)
    assert received == ["Hel", "lo"]
    assert backup.calls == 0 and first.closed
    assert rows == [("groq", "error")]
    assert router.routes[0].breaker._failures == 1  # a mid-answer failure still counts against the provider


@pytest.mark.asyncio
async def test_router_stream_breaker_opens_when_every_stream_dies_mid_answer(rows):
    first = PiecesProvider("groq", mid_error=temporary("groq"))
    backup = PiecesProvider("gemini")
    router = make_router(first, backup, threshold=3)
    for _ in range(3):
        with pytest.raises(ProviderError):
            await collect(router.stream("hi"))
    # a good first piece must not reset the failure count: the breaker is open now
    chunks = await collect(router.stream("hi"))
    assert chunks[-1].final.provider == "gemini"
    assert first.calls == 3


@pytest.mark.asyncio
async def test_router_stream_bug_mid_answer_is_logged_as_error_not_cancelled(rows):
    router = make_router(PiecesProvider("groq", mid_error=KeyError("oops")))
    with pytest.raises(KeyError):
        await collect(router.stream("hi"))
    assert rows == [("groq", "error")]


@pytest.mark.asyncio
async def test_router_stream_all_failed(rows):
    router = make_router(PiecesProvider("groq", error=temporary("groq")))
    with pytest.raises(AllProvidersFailed):
        await collect(router.stream("hi"))


@pytest.mark.asyncio
async def test_router_stream_logs_cancelled_when_the_caller_stops_early(rows):
    provider = PiecesProvider("groq")
    chunks = make_router(provider).stream("hi")
    await anext(chunks)
    await chunks.aclose()
    assert provider.closed
    assert rows == [("groq", "cancelled")]


# ---------- POST /chat/stream ----------


@pytest.fixture
def client(monkeypatch):
    temp_log = RequestLog(":memory:")
    monkeypatch.setattr(main, "request_log", temp_log)
    monkeypatch.setattr(main, "api_keys", ApiKeys([TEST_KEY]))
    monkeypatch.setattr(main, "limiter", TokenBucketLimiter(capacity=1000, refill_per_second=1))
    monkeypatch.setattr(main, "router", make_router(PiecesProvider()))
    yield TestClient(main.app, headers={"X-API-Key": TEST_KEY})
    temp_log.close()


def events(resp) -> list[tuple[str, dict]]:
    out = []
    for block in resp.text.strip().split("\n\n"):
        name, data = block.split("\n")
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def test_stream_sends_chunks_then_done(client):
    resp = client.post("/chat/stream", json={"prompt": "hi"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["x-ratelimit-remaining"]
    got = events(resp)
    assert got[:2] == [("chunk", {"text": "Hel"}), ("chunk", {"text": "lo"})]
    name, done = got[2]
    assert name == "done" and done["provider"] == "fake" and done["usage"]["total_tokens"] == 10
    assert done["cache"] is None and done["complete"] is True
    statuses = main.request_log.db.execute("SELECT status FROM requests").fetchall()
    assert statuses == [("ok",)]


def test_stream_needs_an_api_key(client):
    assert client.post("/chat/stream", json={"prompt": "hi"}, headers={"X-API-Key": "wrong"}).status_code == 401


def test_stream_returns_503_when_every_provider_fails(client, monkeypatch):
    monkeypatch.setattr(main, "router", make_router(PiecesProvider(error=temporary("fake"))))
    resp = client.post("/chat/stream", json={"prompt": "hi"})
    assert resp.status_code == 503


def test_stream_error_mid_answer_sends_error_event_and_caches_nothing(client, monkeypatch):
    monkeypatch.setattr(main, "router", make_router(PiecesProvider(mid_error=temporary("fake"))))
    monkeypatch.setattr(main, "exact_cache", MemoryExactCache(100, 60))
    got = events(client.post("/chat/stream", json={"prompt": "hi"}))
    assert [name for name, _ in got] == ["chunk", "chunk", "error"]
    assert "HTTP 503" in got[-1][1]["detail"]
    assert len(main.exact_cache.lru) == 0


def test_streamed_answer_is_cached_and_the_next_stream_is_a_hit(client, monkeypatch):
    provider = PiecesProvider()
    monkeypatch.setattr(main, "router", make_router(provider))
    monkeypatch.setattr(main, "exact_cache", MemoryExactCache(100, 60))
    client.post("/chat/stream", json={"prompt": "hi"})
    got = events(client.post("/chat/stream", json={"prompt": "hi"}))
    assert provider.calls == 1
    assert got[0] == ("chunk", {"text": "Hello"})
    assert got[1][0] == "done" and got[1][1]["cache"] == "exact"
    # and the normal endpoint sees the same cached answer
    assert client.post("/chat", json={"prompt": "hi"}).json()["cache"] == "exact"


def test_cut_off_streamed_answer_is_not_cached(client, monkeypatch):
    provider = PiecesProvider(complete=False)
    monkeypatch.setattr(main, "router", make_router(provider))
    monkeypatch.setattr(main, "exact_cache", MemoryExactCache(100, 60))
    client.post("/chat/stream", json={"prompt": "hi"})
    client.post("/chat/stream", json={"prompt": "hi"})
    assert provider.calls == 2
