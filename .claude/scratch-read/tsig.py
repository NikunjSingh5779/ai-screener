"""Tests for the Signal Engine: JSON parsing (fenced/embedded/verbose) and the
flip-flop guard."""

from __future__ import annotations

import pytest

from ai_trader.signal import SignalEngine, TradingSignal, extract_json

GOOD_JSON = """{
  "action": "buy",
  "confidence": 72,
  "entry": 245.5,
  "stop_loss": 240.1,
  "target": 252.0,
  "position_size_pct": 4.5,
  "timeframe": "15m",
  "reasoning": "breakout above resistance on volume",
  "market": "NSE",
  "symbol": "RELIANCE"
}"""


def test_parse_plain_json() -> None:
    signal = SignalEngine.parse(GOOD_JSON)
    assert signal.action == "buy"
    assert signal.confidence == 72
    assert signal.entry == 245.5
    assert signal.symbol == "RELIANCE"


def test_parse_fenced_json() -> None:
    signal = SignalEngine.parse("```json\n" + GOOD_JSON + "\n```")
    assert signal.action == "buy"


def test_parse_embedded_json() -> None:
    text = "Sure! Here is my analysis:\n" + GOOD_JSON + "\nHope this helps."
    signal = SignalEngine.parse(text)
    assert signal.action == "buy"


def test_extract_json_brace_matching_with_braces_in_reasoning() -> None:
    text = 'Here you go: {"action":"hold","confidence":50,"reasoning":"{not json}"} thanks'
    assert extract_json(text)["action"] == "hold"


def test_extract_json_no_object_raises() -> None:
    with pytest.raises(ValueError):
        extract_json("I don't see any chart here.")


def test_parse_rejects_unknown_action() -> None:
    with pytest.raises(Exception):
        SignalEngine.parse('{"action":"moon","confidence":50}')


def test_parse_rejects_confidence_out_of_range() -> None:
    with pytest.raises(Exception):
        SignalEngine.parse('{"action":"buy","confidence":150}')


def test_flip_guard_suppresses_rapid_flip() -> None:
    engine = SignalEngine(min_flip_hold_seconds=60)
    now = 1_000_000.0
    buy = TradingSignal(action="buy", confidence=60)
    sell = TradingSignal(action="sell", confidence=60)

    assert engine.guard_flip("X", buy, now=now) is True
    assert engine.guard_flip("X", sell, now=now + 5) is False   # too soon
    assert engine.guard_flip("X", sell, now=now + 61) is True   # after the hold window


def test_flip_guard_allows_same_direction_repeats() -> None:
    engine = SignalEngine(min_flip_hold_seconds=60)
    buy = TradingSignal(action="buy", confidence=60)
    assert engine.guard_flip("X", buy, now=100.0) is True
    assert engine.guard_flip("X", buy, now=105.0) is True  # same direction is fine


def test_flip_guard_ignores_hold() -> None:
    engine = SignalEngine(min_flip_hold_seconds=60)
    hold = TradingSignal(action="hold", confidence=40)
    assert engine.guard_flip("X", hold, now=100.0) is True
