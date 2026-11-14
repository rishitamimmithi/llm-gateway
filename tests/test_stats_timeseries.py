"""Tests for the dashboard's time-series stats (RequestLog.timeseries and GET /stats/timeseries)."""
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.db.logger import RequestLog, percentile

NOW = 1_000_000.0  # a fixed "now", 40 s past a round minute


def insert(log: RequestLog, ts: float, status: str, latency: float = 100.0,
           cost: float | None = 0.0, saved: float | None = None) -> None:
    log.db.execute(
        "INSERT INTO requests (ts, provider, model, prompt_tokens, completion_tokens, latency_ms, status, "
        "cost_usd, saved_usd) VALUES (?, 'p', 'm', 1, 1, ?, ?, ?, ?)",
        (ts, latency, status, cost, saved),
    )


@pytest.fixture
def log():
    request_log = RequestLog(":memory:")
    yield request_log
    request_log.close()


def test_percentile_empty_is_none():
    assert percentile([], 95) is None


def test_percentile_nearest_rank():
    values = [float(v) for v in range(1, 101)]  # 1..100
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 95
    assert percentile(values, 99) == 99
    assert percentile([7.0], 95) == 7


def test_empty_database_gives_zero_buckets(log):
    body = log.timeseries(minutes=5, bucket_seconds=60, now=NOW)
    assert body["latency_ms"] == {"p50": None, "p95": None, "p99": None}
    assert all(b["ok"] == 0 and b["p95_ms"] is None for b in body["buckets"])
    # buckets start on a round minute and cover the whole window up to now
    assert body["buckets"][0]["ts"] % 60 == 0
    assert body["buckets"][0]["ts"] <= NOW - 300
    assert body["buckets"][-1]["ts"] <= NOW < body["buckets"][-1]["ts"] + 60


def test_rows_land_in_the_right_bucket_by_status(log):
    insert(log, NOW - 1, "ok", latency=200, cost=0.002)
    insert(log, NOW - 2, "error", cost=0)
    insert(log, NOW - 3, "cache_exact", cost=0, saved=0.001)
    insert(log, NOW - 4, "cache_semantic", cost=0, saved=0.001)
    insert(log, NOW - 5, "dedup", cost=0, saved=0.002)
    last = log.timeseries(minutes=1, bucket_seconds=3600, now=NOW)["buckets"][-1]
    assert last["ok"] == 1
    assert last["errors"] == 1
    assert last["cache_hits"] == 2
    assert last["deduplicated"] == 1
    assert last["cost_usd"] == pytest.approx(0.002)
    assert last["saved_usd"] == pytest.approx(0.004)
    assert last["p50_ms"] == 200


def test_old_and_future_rows_are_left_out(log):
    insert(log, NOW - 10 * 60, "ok")  # older than the window
    insert(log, NOW + 60, "ok")  # clock skew: after "now"
    body = log.timeseries(minutes=5, bucket_seconds=60, now=NOW)
    assert sum(b["ok"] for b in body["buckets"]) == 0


def test_latency_percentiles_use_only_provider_answers(log):
    for latency in range(1, 21):  # 20 provider answers: 1..20 ms
        insert(log, NOW - 30, "ok", latency=float(latency))
    insert(log, NOW - 30, "cache_exact", latency=0.1)  # fast cache hits must not pull p50 down
    insert(log, NOW - 30, "error", latency=99_999)  # failed attempts aren't answers
    body = log.timeseries(minutes=5, bucket_seconds=60, now=NOW)
    assert body["latency_ms"] == {"p50": 10, "p95": 19, "p99": 20}


def test_rows_split_across_buckets(log):
    insert(log, NOW - 30, "ok", latency=50)
    insert(log, NOW - 150, "ok", latency=300)
    buckets = log.timeseries(minutes=5, bucket_seconds=60, now=NOW)["buckets"]
    with_rows = [b for b in buckets if b["ok"]]
    assert [b["p50_ms"] for b in with_rows] == [300, 50]  # oldest first


def test_only_provider_answers_count_as_spent(log):
    insert(log, NOW - 10, "ok", cost=0.002)
    insert(log, NOW - 10, "error", cost=0.005)  # matches stats(): failed attempts aren't "spent"
    buckets = log.timeseries(minutes=5, bucket_seconds=60, now=NOW)["buckets"]
    assert sum(b["cost_usd"] for b in buckets) == pytest.approx(0.002)


def test_oldest_bucket_is_complete(log):
    # window starts 40 s into a minute; a row 10 s before that is still in the first (round-minute) bucket
    first_bucket_start = NOW - 300 - 40
    insert(log, first_bucket_start + 5, "ok")
    first = log.timeseries(minutes=5, bucket_seconds=60, now=NOW)["buckets"][0]
    assert first["ts"] == first_bucket_start
    assert first["ok"] == 1


def test_null_status_and_latency_do_not_crash(log):
    insert(log, NOW - 10, None)
    insert(log, NOW - 10, "ok", latency=None)
    body = log.timeseries(minutes=5, bucket_seconds=60, now=NOW)
    assert sum(b["ok"] for b in body["buckets"]) == 1
    assert body["latency_ms"]["p50"] is None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "request_log", RequestLog(":memory:"))
    with TestClient(main.app) as test_client:
        yield test_client


def test_endpoint_returns_buckets(client):
    main.request_log.log("p", "m", 1, 1, 120.0, "ok", 0.001)
    body = client.get("/stats/timeseries?minutes=10&bucket=60").json()
    assert body["minutes"] == 10 and body["bucket_seconds"] == 60
    assert sum(b["ok"] for b in body["buckets"]) == 1
    assert body["latency_ms"]["p95"] == 120


@pytest.mark.parametrize("query", ["minutes=0", "bucket=5", "minutes=100000", "minutes=10080&bucket=10", "minutes=1&bucket=86400"])
def test_endpoint_rejects_bad_ranges(client, query):
    assert client.get(f"/stats/timeseries?{query}").status_code == 422
