import time

import pytest

from app import main
from app.core.cost import estimate_cost
from app.providers.fake import FAKE_COMPLETION_TOKENS, FakeProvider


@pytest.mark.asyncio
async def test_fake_provider_waits_then_answers():
    provider = FakeProvider(delay_seconds=0.05)
    start = time.perf_counter()
    resp = await provider.chat("What is the capital of Australia?")
    assert time.perf_counter() - start >= 0.04  # Windows timers can wake a few ms early
    assert resp.provider == "fake" and resp.model == "fake-llm"
    assert resp.complete
    assert resp.prompt_tokens > 0 and resp.completion_tokens == FAKE_COMPLETION_TOKENS
    assert "capital of Australia" in resp.text


@pytest.mark.asyncio
async def test_fake_provider_with_no_delay():
    resp = await FakeProvider(delay_seconds=0).chat("")
    assert resp.prompt_tokens == 1  # never 0, even for an empty prompt


def test_fake_provider_is_built_from_provider_order(monkeypatch):
    monkeypatch.setattr(main.config, "PROVIDER_ORDER", ["fake"])
    router = main.build_router()
    assert [route.provider.name for route in router.routes] == ["fake"]


def test_fake_model_has_a_price():
    assert estimate_cost("fake-llm", 1_000_000, 0, main.config.MODEL_PRICES) is not None
