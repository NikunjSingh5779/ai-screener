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


def test_parse_coerces_model_number_formats() -> None:
    signal = SignalEngine.parse(
        """{
          "action": "buy",
          "confidence": "high",
          "entry": "₹24,583",
          "stop_loss": "24,000.50",
          "target": "$25,100",
          "position_size_pct": "4.5%"
        }"""
    )
    assert signal.confidence == 75.0
    assert signal.entry == 24583.0
    assert signal.stop_loss == 24000.5
    assert signal.target == 25100.0
    assert signal.position_size_pct == 4.5


def test_parse_replaces_invalid_model_number_with_none() -> None:
    signal = SignalEngine.parse(
        '{"action":"hold","confidence":null,"entry":"not visible"}'
    )
    assert signal.confidence == 50.0
    assert signal.entry is None


def test_parse_allows_null_optional_text_fields() -> None:
    signal = SignalEngine.parse(
        '{"action":"buy","confidence":70,"timeframe":null,'
        '"reasoning":null,"market":null,"symbol":null}'
    )
    assert signal.timeframe == ""
    assert signal.reasoning == ""
    assert signal.market == ""
    assert signal.symbol == ""


def test_parse_logs_raw_text_for_schema_validation_failure(caplog: pytest.LogCaptureFixture) -> None:
    text = '{"action":"buy","confidence":150}'
    with pytest.raises(Exception), caplog.at_level("ERROR"):
        SignalEngine.parse(text)
    assert text in caplog.text


def test_parse_logs_raw_text_for_non_json_model_output(caplog: pytest.LogCaptureFixture) -> None:
    text = "I cannot determine a trading signal from this image."
    with pytest.raises(ValueError), caplog.at_level("ERROR"):
        SignalEngine.parse(text)
    assert text in caplog.text


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


# --- Task 1: per-market prompt tuning -----------------------------------------

MARKET_NOTES = {
    "NSE": "NSE cash market. Currency INR. Typical hours 09:15-15:30 IST.",
    "US": (
        "US equities. Currency USD ($). Typical hours 09:30-16:00 ET; "
        "be mindful of extended-hours session context."
    ),
}


def test_build_prompt_injects_selected_market_note() -> None:
    engine = SignalEngine(market="NSE", market_notes=MARKET_NOTES)
    prompt = engine.build_prompt()
    assert "NSE cash market. Currency INR." in prompt
    assert "US equities." not in prompt


def test_build_prompt_other_market_gets_its_own_note() -> None:
    engine = SignalEngine(market="US", market_notes=MARKET_NOTES)
    prompt = engine.build_prompt()
    assert "US equities." in prompt
    assert "NSE cash market." not in prompt


def test_build_prompt_unknown_market_renders_cleanly() -> None:
    engine = SignalEngine(market="Futures", market_notes=MARKET_NOTES)
    prompt = engine.build_prompt()
    assert "Market notes:" in prompt
    assert "{market_notes}" not in prompt


def test_build_prompt_without_market_notes_renders_cleanly() -> None:
    engine = SignalEngine(market="NSE")
    prompt = engine.build_prompt()
    assert "Market notes:" in prompt
    assert "{market_notes}" not in prompt
