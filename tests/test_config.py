"""
Config number checks: silly values must stop the gateway at startup with a clear message.
"""
import pytest

from app.config import read_number


@pytest.mark.parametrize("raw, expected", [("10", 10.0), ("0.5", 0.5), ("1e1", 10.0)])
def test_good_positive_numbers_are_accepted(monkeypatch, raw, expected):
    monkeypatch.setenv("TEST_SETTING", raw)
    assert read_number("TEST_SETTING", "1") == expected


def test_default_is_used_when_setting_is_missing(monkeypatch):
    monkeypatch.delenv("TEST_SETTING", raising=False)
    assert read_number("TEST_SETTING", "7") == 7.0


@pytest.mark.parametrize("raw", ["0", "-1", "nan", "inf", "-inf"])
def test_zero_negative_nan_and_infinity_are_rejected(monkeypatch, raw):
    monkeypatch.setenv("TEST_SETTING", raw)
    with pytest.raises(ValueError, match="TEST_SETTING must be a positive number"):
        read_number("TEST_SETTING", "1")


def test_text_is_rejected_with_a_clear_message(monkeypatch):
    monkeypatch.setenv("TEST_SETTING", "ten")
    with pytest.raises(ValueError, match="TEST_SETTING must be a number, got 'ten'"):
        read_number("TEST_SETTING", "1")


def test_allow_zero_accepts_zero_but_not_negative(monkeypatch):
    monkeypatch.setenv("TEST_SETTING", "0")
    assert read_number("TEST_SETTING", "1", allow_zero=True) == 0.0
    monkeypatch.setenv("TEST_SETTING", "-0.5")
    with pytest.raises(ValueError, match="zero or positive"):
        read_number("TEST_SETTING", "1", allow_zero=True)


@pytest.mark.parametrize("raw", ["0", "3.5", "-2"])
def test_max_attempts_style_integer_rules(monkeypatch, raw):
    monkeypatch.setenv("TEST_SETTING", raw)
    with pytest.raises(ValueError):
        read_number("TEST_SETTING", "3", cast=int)  # 0 and -2 are not positive; "3.5" is not an int


def test_integer_setting_returns_an_int(monkeypatch):
    monkeypatch.setenv("TEST_SETTING", "3")
    value = read_number("TEST_SETTING", "1", cast=int)
    assert value == 3 and isinstance(value, int)
