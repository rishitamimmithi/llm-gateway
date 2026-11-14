"""DEMO_SEED: an empty request log gets ~150 made-up rows from the fake provider, spread over the last hour."""
import json
import random
from pathlib import Path

import pytest

import app.main as main
from app import demo
from app.db.logger import RequestLog
from app.providers.base import Provider, ProviderResponse
from app.providers.fake import FakeProvider
from tests.test_api import client  # noqa: F401  (fixture)

MIX = json.loads((Path(__file__).parent.parent / "loadtest" / "prompt_mix.json").read_text(encoding="utf-8"))
PRICES = {"fake-llm": (0.075, 0.30)}
NOW = 1_800_000_000.0


class CountingFake(FakeProvider):
    def __init__(self) -> None:
        super().__init__(delay_seconds=0)
        self.calls = 0

    async def chat(self, prompt: str) -> ProviderResponse:
        self.calls += 1
        return await super().chat(prompt)


async def run_seed(log: RequestLog, provider: Provider, n: int = 150) -> int:
    rng = random.Random(7)
    return await demo.seed(
        log, demo.pick_prompts(MIX, n, rng), provider, prices=PRICES,
        provider_ms=lambda: 300.0, hit_ms=lambda: 1.0, now=NOW, rng=rng,
    )


def test_pick_prompts_mixes_repeats_and_one_offs():
    picks = demo.pick_prompts(MIX, 150, random.Random(1))
    assert len(picks) == 150
    assert len(set(picks)) < 150  # popular questions repeat, so the cache has something to hit
    assert set(MIX["one_offs"][:30]) <= set(picks)


@pytest.mark.asyncio
async def test_seed_writes_fake_rows_over_the_last_hour_with_cache_hits():
    log = RequestLog(":memory:")
    provider = CountingFake()
    assert await run_seed(log, provider) == 150

    rows = log.db.execute("SELECT ts, provider, status, cost_usd, saved_usd, prompt_preview FROM requests").fetchall()
    assert len(rows) == 150
    assert {r[1] for r in rows} == {"fake"}
    assert all(NOW - 3600 <= r[0] < NOW for r in rows)
    statuses = [r[2] for r in rows]
    assert statuses.count("ok") == provider.calls  # one provider call per distinct prompt...
    assert statuses.count("cache_exact") == 150 - provider.calls  # ...every repeat is a cache hit
    assert all(r[5] for r in rows)  # previews are shown: these prompts are mine, not users'
    assert log.has_demo_rows() and all(r["demo"] for r in log.recent(500))
    cache = log.cache_stats()
    assert cache["hit_rate"] > 0.3 and cache["saved_usd"] > 0
    log.close()


@pytest.mark.asyncio
async def test_seed_never_touches_a_log_that_already_has_rows():
    log = RequestLog(":memory:")
    log.log("groq", "real-model", 5, 5, 100.0, "ok", 0.001)
    provider = CountingFake()
    assert await run_seed(log, provider) == 0
    assert provider.calls == 0
    assert len(log.recent(500)) == 1
    log.close()


@pytest.mark.asyncio
async def test_seed_from_file_never_raises(tmp_path):
    log = RequestLog(":memory:")
    written = await demo.seed_from_file(
        log, str(tmp_path / "missing.json"), FakeProvider(delay_seconds=0), n=10, prices=PRICES, provider_delay_ms=300
    )
    assert written == 0
    log.close()


def test_badge_follows_the_rows_not_the_setting(client, monkeypatch):
    monkeypatch.setattr(main.config, "DEMO_SEED", True)
    assert client.get("/stats").json()["demo_data"] is False  # setting on, but no demo rows yet
    main.request_log.log("fake", "fake-llm", 1, 1, 1.0, "ok", 0.0, "demo", ts=NOW, demo=True)
    monkeypatch.setattr(main.config, "DEMO_SEED", False)
    body = client.get("/stats").json()
    assert body["demo_data"] is True  # setting off, but the database still holds demo rows
    assert client.get("/stats/recent").json()[0]["demo"] is True


def test_startup_seed_never_calls_the_configured_providers(monkeypatch):
    """Even with real providers in the router, demo rows come only from the built-in fake provider."""
    from fastapi.testclient import TestClient

    from tests.test_api import TEST_KEY, FakeProvider as RouterProvider, make_router

    real = RouterProvider("groq")
    monkeypatch.setattr(main, "router", make_router(real))
    monkeypatch.setattr(main, "request_log", RequestLog(":memory:"))
    monkeypatch.setattr(main.config, "DEMO_SEED", True)
    monkeypatch.setattr(main.config, "DEMO_SEED_REQUESTS", 40)
    with TestClient(main.app, headers={"X-API-Key": TEST_KEY}) as client:  # runs the startup seed
        for _ in range(200):
            if len(client.get("/stats/recent?limit=500").json()) == 40:
                break
        rows = client.get("/stats/recent?limit=500").json()
    assert len(rows) == 40 and all(r["demo"] and r["provider"] == "fake" for r in rows)
    assert real.calls == 0


def test_recent_orders_by_time_so_backdated_rows_sort_correctly():
    log = RequestLog(":memory:")
    log.log("fake", "fake-llm", 1, 1, 1.0, "ok", 0.0, "new", ts=NOW)
    log.log("fake", "fake-llm", 1, 1, 1.0, "ok", 0.0, "old", ts=NOW - 600)  # written later, happened earlier
    assert [r["prompt_preview"] for r in log.recent(2)] == ["new", "old"]
    log.close()
