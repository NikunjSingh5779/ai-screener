"""Tests for high-confidence alerts (Task 3)."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from ai_trader.alerts import alert, should_alert
from ai_trader.signal import TradingSignal


def test_should_alert_buy_at_threshold() -> None:
    """True: buy at exactly threshold."""
    signal = TradingSignal(action="buy", confidence=80.0)
    assert should_alert(signal, 80.0) is True


def test_should_alert_sell_at_threshold() -> None:
    """True: sell at exactly threshold."""
    signal = TradingSignal(action="sell", confidence=80.0)
    assert should_alert(signal, 80.0) is True


def test_should_alert_buy_above_threshold() -> None:
    """True: buy above threshold."""
    signal = TradingSignal(action="buy", confidence=90.0)
    assert should_alert(signal, 80.0) is True


def test_should_alert_false_hold() -> None:
    """False: hold regardless of confidence."""
    signal = TradingSignal(action="hold", confidence=95.0)
    assert should_alert(signal, 80.0) is False


def test_should_alert_false_watch() -> None:
    """False: watch regardless of confidence."""
    signal = TradingSignal(action="watch", confidence=95.0)
    assert should_alert(signal, 80.0) is False


def test_should_alert_false_below_threshold() -> None:
    """False: buy/sell below threshold."""
    signal = TradingSignal(action="buy", confidence=79.0)
    assert should_alert(signal, 80.0) is False


def test_alert_calls_beep_fn_once_for_qualifying() -> None:
    """alert() calls injected beep_fn exactly once for qualifying signal."""
    beep_mock = Mock()
    signal = TradingSignal(action="buy", confidence=85.0)
    result = alert(signal, 80.0, beep_fn=beep_mock)
    assert result is True
    beep_mock.assert_called_once()


def test_alert_calls_beep_fn_zero_for_non_qualifying() -> None:
    """alert() calls injected beep_fn zero times for non-qualifying signal."""
    beep_mock = Mock()
    signal = TradingSignal(action="hold", confidence=95.0)
    result = alert(signal, 80.0, beep_fn=beep_mock)
    assert result is False
    beep_mock.assert_not_called()


def test_alert_returns_false_for_below_threshold() -> None:
    """alert() returns False for buy/sell below threshold."""
    beep_mock = Mock()
    signal = TradingSignal(action="sell", confidence=70.0)
    result = alert(signal, 80.0, beep_fn=beep_mock)
    assert result is False
    beep_mock.assert_not_called()