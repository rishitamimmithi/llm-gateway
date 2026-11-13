"""
Tests for the shared provider helpers in app/providers/base.py.
"""
import asyncio
import time

import httpx
import pytest

from app.providers.base import ProviderError, error_message, retry_after_seconds, send_request, to_count


def google_429(details) -> httpx.Response:
    return httpx.Response(429, json={"error": {"code": 429, "message": "quota", "details": details}})


# ---------- to_count ----------

@pytest.mark.parametrize(
    "value, expected",
    [(7, 7), ("12", 12), (None, 0), ("abc", 0), (float("nan"), 0), (float("inf"), 0), (-3, 0), ([], 0)],
)
def test_to_count_never_crashes(value, expected):
    assert to_count(value) == expected


# ---------- retry_after_seconds ----------

@pytest.mark.parametrize(
    "details, expected",
    [
        ([{"retryDelay": "30s"}], 30.0),
        ([{"retryDelay": "1.5s"}], 1.5),
        ([{"retryDelay": "abcs"}, {"retryDelay": "7s"}], 7.0),  # skip a broken entry, keep looking
        ([{"retryDelay": "-5s"}], None),
        ([{"retryDelay": "30"}], None),  # Google's format always ends in "s"
        ("not a list", None),
        ([], None),
    ],
)
def test_retry_after_seconds_from_google_body(details, expected):
    assert retry_after_seconds(google_429(details)) == expected


def test_retry_after_seconds_with_no_hint_at_all():
    assert retry_after_seconds(httpx.Response(429, text="slow down")) is None


# ---------- error_message ----------

def test_error_message_with_empty_secret_is_not_garbled():
    resp = httpx.Response(500, json={"error": {"message": "boom"}})
    assert error_message(resp, "") == "boom"


@pytest.mark.parametrize(
    "resp",
    [
        httpx.Response(500, text="plain text failure"),
        httpx.Response(500, json=[{"error": {"message": "inside a list"}}]),
    ],
)
def test_error_message_falls_back_to_raw_text(resp):
    assert error_message(resp, "key") == resp.text[:200]


# ---------- send_request ----------

@pytest.mark.asyncio
async def test_send_request_has_a_total_deadline():
    # A server that keeps the call open longer than the timeout must be cut off, as a retryable timeout.
    async def slow_server(request):
        await asyncio.sleep(5)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow_server)) as client:
        start = time.perf_counter()
        with pytest.raises(ProviderError) as exc:
            await send_request("test", client, "https://example.com", json={}, headers={}, timeout=0.1, secret="k")
        elapsed = time.perf_counter() - start

    assert exc.value.retryable is True
    assert "timed out" in str(exc.value)
    assert elapsed < 1.0  # stopped at ~0.1 s, not after the server's 5 s


@pytest.mark.asyncio
async def test_outside_cancellation_is_not_mislabelled_as_a_timeout():
    # Only OUR stopwatch should become "timed out". A cancel from outside (e.g. client hung up)
    # must pass through as CancelledError, so it is never retried or logged as a provider failure.
    async def slow_server(request):
        await asyncio.sleep(5)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow_server)) as client:
        task = asyncio.create_task(
            send_request("test", client, "https://example.com", json={}, headers={}, timeout=10, secret="k")
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_send_request_with_unencodable_key_is_a_clean_error():
    # A curly quote pasted into .env can't be sent in an HTTP header.
    bad_key = "AIza’oops"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200))) as client:
        with pytest.raises(ProviderError) as exc:
            await send_request(
                "test", client, "https://example.com", json={}, headers={"x-key": bad_key}, timeout=5, secret=bad_key
            )
    assert exc.value.retryable is False
    assert "could not build request" in str(exc.value)
    assert bad_key not in str(exc.value)
