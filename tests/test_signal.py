"""Tests for the Signal Engine: JSON parsing (fenced/embedded/verbose) and the
flip-flop guard."""

from __future__ import annotations

import json

import pytest

from ai_trader.signal import SignalEngine, TradingSignal, extract_json

GOOD_JSON = """{
  "action": "buy",
  "confidence": 72,
  "entry": 245.5,
  "stop_loss": 240.1,
  "target": 255.0,
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
          "target": "$25,500",
          "position_size_pct": "4.5%"
        }"""
    )
    assert signal.confidence == 75.0
    assert signal.entry == 24583.0
    assert signal.stop_loss == 24000.5
    assert signal.target == 25500.0
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


# --- Task: structural sanity check (target/entry/stop ordering) -------------


def test_parse_downgrades_backwards_sell_to_hold() -> None:
    """A sell with target ABOVE stop (target 120 > entry 100 < stop 105) is
    structurally impossible — it must downgrade to hold, never reach the
    overlay/logger as a short."""
    signal = SignalEngine.parse(
        """{
          "action": "sell",
          "confidence": 80,
          "entry": 100,
          "stop_loss": 105,
          "target": 120,
          "timeframe": "15m",
          "reasoning": "short the breakdown",
          "market": "NSE",
          "symbol": "XYZ"
        }"""
    )
    assert signal.action == "hold"
    assert "[STRUCTURALLY INVALID — downgraded from sell]" in signal.reasoning
    assert "original action sell, entry 100.0, stop_loss 105.0, target 120.0" in signal.reasoning
    assert "short the breakdown" in signal.reasoning  # original rationale retained


def test_parse_downgrades_backwards_buy_to_hold() -> None:
    """A buy with target BELOW stop (entry 100 > stop 105 > target 95) is
    impossible long — downgraded to hold."""
    signal = SignalEngine.parse(
        """{
          "action": "buy",
          "confidence": 80,
          "entry": 100,
          "stop_loss": 105,
          "target": 95,
          "timeframe": "15m",
          "reasoning": "buy the dip",
          "market": "NSE",
          "symbol": "XYZ"
        }"""
    )
    assert signal.action == "hold"
    assert "[STRUCTURALLY INVALID — downgraded from buy]" in signal.reasoning
    assert "original action buy, entry 100.0, stop_loss 105.0, target 95.0" in signal.reasoning
    assert "buy the dip" in signal.reasoning


def test_parse_keeps_valid_buy_levels() -> None:
    """A proper long (target > entry > stop) passes through unchanged."""
    signal = SignalEngine.parse(
        """{
          "action": "buy",
          "confidence": 72,
          "entry": 245.5,
          "stop_loss": 240.1,
          "target": 255.0,
          "timeframe": "15m",
          "reasoning": "breakout",
          "market": "NSE",
          "symbol": "RELIANCE"
        }"""
    )
    assert signal.action == "buy"
    assert signal.reasoning == "breakout"
    assert "STRUCTURALLY INVALID" not in signal.reasoning


def test_parse_keeps_valid_sell_levels() -> None:
    """A proper short (target < entry < stop) passes through unchanged."""
    signal = SignalEngine.parse(
        """{
          "action": "sell",
          "confidence": 70,
          "entry": 250.0,
          "stop_loss": 255.0,
          "target": 242.0,
          "timeframe": "15m",
          "reasoning": "distribution",
          "market": "NSE",
          "symbol": "HDFC"
        }"""
    )
    assert signal.action == "sell"
    assert signal.reasoning == "distribution"


def test_parse_skips_check_when_levels_missing() -> None:
    """No levels -> no structural verdict; a bare buy stays buy (levels absent
    means the model could not see them; not an ordering error)."""
    signal = SignalEngine.parse(
        '{"action":"buy","confidence":60,"entry":null,"stop_loss":null,"target":null}'
    )
    assert signal.action == "buy"


@pytest.mark.parametrize(
    ("action", "entry", "stop", "target", "expected_ratio"),
    [
        ("buy", 212.19, 211.64, 212.30, "0.20:1"),
        ("sell", 100.0, 105.0, 99.0, "0.20:1"),
    ],
)
def test_parse_downgrades_inadequate_reward_risk_to_watch(
    action: str, entry: float, stop: float, target: float, expected_ratio: str
) -> None:
    signal = SignalEngine.parse(
        json.dumps(
            {
                "action": action,
                "confidence": 85,
                "entry": entry,
                "stop_loss": stop,
                "target": target,
                "position_size_pct": 2.0,
                "timeframe": "15m",
                "reasoning": "Illustrative setup.",
                "market": "US",
                "symbol": "NVDA",
            }
        )
    )
    assert signal.action == "watch"
    assert signal.entry is None
    assert signal.stop_loss is None
    assert signal.target is None
    assert signal.position_size_pct is None
    assert expected_ratio in signal.reasoning
    assert "minimum 1.50:1" in signal.reasoning
    assert "original entry" in signal.reasoning


def test_parse_keeps_setup_at_minimum_reward_risk_ratio() -> None:
    # Reward 1.5, risk 1.0 => exactly the configured 1.5:1 threshold.
    signal = SignalEngine.parse(
        '{"action":"buy","confidence":70,"entry":100,"stop_loss":99,'
        '"target":101.5,"timeframe":"15m","market":"US","symbol":"TEST"}'
    )
    assert signal.action == "buy"
    assert signal.entry == 100.0
    assert signal.stop_loss == 99.0
    assert signal.target == 101.5


def test_parse_keeps_hold_with_backwards_levels() -> None:
    """hold/watch are never downgraded — the check only guards directional calls."""
    signal = SignalEngine.parse(
        '{"action":"hold","confidence":50,"entry":100,"stop_loss":105,"target":120}'
    )
    assert signal.action == "hold"
    assert "STRUCTURALLY INVALID" not in signal.reasoning


def test_parse_downgrade_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """The downgrade is visible in the logs — we want to SEE when this fires,
    not silently suppress a bad trade."""
    with caplog.at_level("WARNING", logger="ai_trader.signal"):
        SignalEngine.parse(
            """{
              "action": "sell",
              "confidence": 80,
              "entry": 100,
              "stop_loss": 105,
              "target": 120,
              "reasoning": "short the breakdown"
            }"""
        )
    messages = [r.getMessage() for r in caplog.records if r.levelno >= 30]
    assert any("structurally invalid sell" in m and "downgraded to hold" in m for m in messages)


def test_parse_coerces_action_case_and_synonyms() -> None:
    """Action case variations and model synonyms normalize to valid actions."""
    assert SignalEngine.parse('{"action":"BUY","confidence":50}').action == "buy"
    assert SignalEngine.parse('{"action":" Strong Buy ","confidence":50}').action == "buy"
    assert SignalEngine.parse('{"action":"Sell","confidence":50}').action == "sell"
    assert SignalEngine.parse('{"action":"neutral","confidence":50}').action == "hold"
    assert SignalEngine.parse('{"action":"wait","confidence":50}').action == "watch"


def test_parse_wipes_hallucinated_quantity() -> None:
    """A model hallucinating quantity in the JSON output is wiped to None."""
    signal = SignalEngine.parse('{"action":"hold","confidence":50,"quantity":1234}')
    assert signal.quantity is None


def test_parse_coerces_price_with_currency_suffix_or_tilde() -> None:
    """Price fields containing currency text or tildes are parsed cleanly."""
    signal = SignalEngine.parse(
        """{
          "action": "hold",
          "confidence": 50,
          "entry": "24,583 INR",
          "stop_loss": "~24000.5",
          "target": "25500 USD"
        }"""
    )
    assert signal.entry == 24583.0
    assert signal.stop_loss == 24000.5
    assert signal.target == 25500.0



def test_prompt_requires_evidence_and_watch_for_ambiguous_chart() -> None:
    prompt = SignalEngine().build_prompt()
    assert "Never invent a price" in prompt
    assert 'return "watch" or "hold"' in prompt
    assert "not a probability of profit" in prompt
    assert "short intraday trades only" in prompt
    assert "For daily/weekly or unknown timeframe, return \"watch\"" in prompt


def test_prompt_explicitly_rejects_crypto_pairs() -> None:
    prompt = SignalEngine().build_prompt()
    assert "listed equities only" in prompt
    assert "cryptocurrency pair" in prompt
    assert "BTCUSD" in prompt
    assert 'return "watch" with null actionable levels' in prompt


def test_prompt_includes_detailed_chart_reading_checklist() -> None:
    prompt = SignalEngine().build_prompt()
    assert "Chart-reading procedure" in prompt
    assert "latest price label, price scale and time axis" in prompt
    assert "Never invent a price" in prompt
    assert "Do not claim a candle has closed" in prompt
    assert "reasoning" in prompt
    assert "at least 1.5 times the entry-to-stop distance" in prompt


def test_auto_market_prompt_contains_indian_and_us_guidance() -> None:
    prompt = SignalEngine(market="AUTO", market_notes=MARKET_NOTES).build_prompt()
    assert "Market hint: AUTO" in prompt
    assert "NSE cash market" in prompt
    assert "US equities" in prompt
    assert "visible ticker/exchange label" in prompt


@pytest.mark.parametrize(
    ("market", "symbol", "expected_market", "expected_symbol", "currency"),
    [
        ("NSE", "NSE:RELIANCE", "NSE", "RELIANCE", "INR"),
        ("BSE", "BSE:TCS", "BSE", "TCS", "INR"),
        ("NASDAQ", "NASDAQ:TSLA", "US", "TSLA", "USD"),
        ("NYSE", "NYSE:IBM", "US", "IBM", "USD"),
        ("US equities", "AAPL", "US", "AAPL", "USD"),
    ],
)
def test_parse_normalizes_supported_market_symbol_and_currency(
    market: str,
    symbol: str,
    expected_market: str,
    expected_symbol: str,
    currency: str,
) -> None:
    import json

    signal = SignalEngine.parse(json.dumps({
        "action": "hold",
        "confidence": 50,
        "market": market,
        "symbol": symbol,
    }))
    assert signal.market == expected_market
    assert signal.symbol == expected_symbol
    assert signal.currency == currency


def test_parse_does_not_invent_currency_for_unknown_market() -> None:
    signal = SignalEngine.parse(
        '{"action":"hold","confidence":50,"market":"LSE","symbol":"ABC"}'
    )
    assert signal.market == "LSE"
    assert signal.currency is None
