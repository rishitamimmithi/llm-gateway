"""
Tests for turning token counts into dollars, and for the cost column in the request log.
"""
import sqlite3

import pytest

from app import config
from app.core.cost import estimate_cost
from app.db.logger import RequestLog
from app.providers.gemini import GeminiProvider

PRICES = {"cheap": (0.10, 0.50)}  # USD per 1M input / output tokens


def test_cost_uses_separate_input_and_output_prices():
    # 1000 * 0.10/1M + 2000 * 0.50/1M = 0.0001 + 0.001
    assert estimate_cost("cheap", 1000, 2000, PRICES) == pytest.approx(0.0011)


def test_one_million_tokens_costs_exactly_the_listed_price():
    assert estimate_cost("cheap", 1_000_000, 0, PRICES) == pytest.approx(0.10)
    assert estimate_cost("cheap", 0, 1_000_000, PRICES) == pytest.approx(0.50)


def test_zero_tokens_cost_nothing():
    assert estimate_cost("cheap", 0, 0, PRICES) == 0


def test_unknown_model_is_none_not_free():
    assert estimate_cost("mystery", 100, 100, PRICES) is None


def test_default_models_all_have_prices():
    assert config.MODEL in config.MODEL_PRICES
    # look up the name the provider actually reports (it drops a "models/" prefix)
    assert GeminiProvider("k", config.GEMINI_MODEL).model in config.MODEL_PRICES


def test_tiny_costs_are_not_rounded_away():
    log = RequestLog(":memory:")
    log.log("groq", "m", 10, 10, 1.0, "ok", 0.0000012)
    stats = log.stats()
    log.close()
    assert stats["total_cost_usd"] == pytest.approx(0.0000012)
    assert stats["by_provider"]["groq"]["total_cost_usd"] == pytest.approx(0.0000012)


def test_cost_defaults_to_unknown_when_not_given():
    log = RequestLog(":memory:")
    log.log("groq", "m", 1, 1, 1.0, "ok")
    assert log.stats()["unpriced_requests"] == 1
    log.close()


def test_price_table_has_no_negative_prices():
    for model, (input_price, output_price) in config.MODEL_PRICES.items():
        assert input_price >= 0 and output_price >= 0, model


def test_old_database_gets_a_cost_column(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE requests (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, provider TEXT, model TEXT, "
        "prompt_tokens INTEGER, completion_tokens INTEGER, latency_ms REAL, status TEXT)"
    )
    old.execute("INSERT INTO requests VALUES (1, 0, 'groq', 'm', 1, 1, 10.0, 'ok')")
    old.commit()
    old.close()

    log = RequestLog(path)
    log.log("groq", "m", 1, 1, 10.0, "ok", 0.5)
    stats = log.stats()
    log.close()

    assert stats["successful_requests"] == 2
    assert stats["total_cost_usd"] == 0.5
    assert stats["unpriced_requests"] == 1  # the old row has no cost
