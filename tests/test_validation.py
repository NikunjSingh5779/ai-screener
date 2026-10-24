"""Tests for deterministic, model-independent signal safety guards."""
import math
import pytest
from ai_trader.signal import TradingSignal
from ai_trader.validation import downgrade_unsafe_signal, looks_like_crypto_symbol, validate_signal_safety

@pytest.mark.parametrize("symbol", ["BTCUSD","BTCUSDT","ETHUSD","BINANCE:SOLUSDT","BTC"])
def test_crypto_symbols_are_detected(symbol): assert looks_like_crypto_symbol(symbol)

@pytest.mark.parametrize("symbol", ["AAPL","NVDA","RELIANCE","HDFCBANK"])
def test_equity_symbols_are_not_crypto(symbol): assert not looks_like_crypto_symbol(symbol)

def valid_signal(**overrides):
    values=dict(action="sell",market="US",symbol="NVDA",entry=140.0,stop_loss=143.0,target=134.0)
    values.update(overrides); return values

def test_valid_sell_risk_levels_pass_deterministically():
    assert validate_signal_safety(**valid_signal()) == []

def test_valid_buy_risk_levels_pass_deterministically():
    assert validate_signal_safety(**valid_signal(action="buy",entry=140,stop_loss=137,target=146)) == []

@pytest.mark.parametrize("action,entry,stop,target",[
    ("sell",140,137,134),("buy",140,143,146),("buy",140,137,142),("sell",140,143,138)])
def test_invalid_or_low_reward_risk_levels_are_blocked(action,entry,stop,target):
    assert validate_signal_safety(**valid_signal(action=action,entry=entry,stop_loss=stop,target=target))

@pytest.mark.parametrize("value",[math.nan,math.inf,-math.inf])
def test_non_finite_risk_levels_are_blocked(value):
    assert validate_signal_safety(**valid_signal(entry=value))

def test_missing_symbol_and_unknown_market_are_blocked():
    errors=validate_signal_safety(**valid_signal(symbol="",market=""))
    assert any("symbol" in e for e in errors) and any("market" in e for e in errors)

def test_configured_market_and_symbol_mismatch_are_blocked():
    errors=validate_signal_safety(**valid_signal(symbol="AAPL",expected_market="NSE",symbol_hint="NVDA"))
    assert any("configured market" in e for e in errors) and any("symbol" in e for e in errors)

def test_crypto_is_blocked_even_if_symbol_matches():
    assert any("cryptocurrency" in e for e in validate_signal_safety(**valid_signal(symbol="BTCUSD",expected_market="US")))

def test_stale_data_is_blocked_using_explicit_age_metadata():
    assert any("stale" in e for e in validate_signal_safety(**valid_signal(data_age_seconds=61,freshness_verified=True)))

def test_fresh_data_requires_verified_metadata_when_age_is_supplied():
    assert any("unverified" in e for e in validate_signal_safety(**valid_signal(data_age_seconds=5,freshness_verified=False)))

def test_unknown_age_is_not_falsely_claimed_stale():
    assert validate_signal_safety(**valid_signal(data_age_seconds=None)) == []

def test_downgrade_clears_actionable_levels_and_quantity():
    signal=TradingSignal(action="sell",confidence=90,entry=140,stop_loss=143,target=134,
        position_size_pct=1,quantity=5,market="US",symbol="NVDA",reasoning="synthetic signal")
    result=downgrade_unsafe_signal(signal,["market data is stale"])
    assert result.action=="watch"
    assert result.entry is result.stop_loss is result.target is None
    assert result.position_size_pct is result.quantity is None
    assert "market data is stale" in result.reasoning
