"""End-to-end tests for deterministic safety guards in the screenshot read pipeline."""

from __future__ import annotations

import json

import pytest
from PIL import Image

from ai_trader.cli import read_signal
from ai_trader.config import load_config
from ai_trader.providers import ProviderResult
from ai_trader.signal import SignalEngine


class StaticClient:
    """Return a fixed synthetic signal without contacting a model provider."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def analyze(self, image_b64: str, mime: str, prompt: str) -> ProviderResult:
        return ProviderResult(text=json.dumps(self.payload), model="test/static")


def _payload(**overrides) -> dict:
    payload = {
        "action": "buy",
        "confidence": 82,
        "entry": 100.0,
        "stop_loss": 98.0,
        "target": 104.0,
        "position_size_pct": 2.0,
        "timeframe": "15m",
        "reasoning": "Synthetic test setup.",
        "market": "NASDAQ",
        "symbol": "NVDA",
    }
    payload.update(overrides)
    return payload


def _read(monkeypatch, payload: dict, *, market: str = "US", symbol_hint: str = ""):
    monkeypatch.setattr(
        "ai_trader.cli.grab_screen",
        lambda cfg: Image.new("RGB", (32, 32), "black"),
    )
    cfg = load_config()
    cfg.market = market
    cfg.symbol_hint = symbol_hint
    engine = SignalEngine(
        market=market,
        symbol_hint=symbol_hint,
        market_notes=cfg.market_notes,
        min_flip_hold_seconds=0.0,
    )
    return read_signal(cfg, StaticClient(payload), engine)


@pytest.mark.parametrize(
    "action,entry,stop_loss,target",
    [
        ("buy", 100.0, 98.0, 101.0),   # reward/risk = 0.5:1
        ("sell", 100.0, 102.0, 99.0),  # reward/risk = 0.5:1
    ],
)
def test_read_pipeline_downgrades_low_reward_risk(
    monkeypatch, action, entry, stop_loss, target
) -> None:
    result = _read(
        monkeypatch,
        _payload(action=action, entry=entry, stop_loss=stop_loss, target=target),
    )

    assert result.ctx is not None
    signal = result.ctx.signal
    assert signal.action == "watch"
    assert signal.entry is None
    assert signal.stop_loss is None
    assert signal.target is None
    assert signal.position_size_pct is None
    assert signal.quantity is None
    assert "[INADEQUATE REWARD/RISK — downgraded" in signal.reasoning
    assert "potential reward/risk 0.50:1" in signal.reasoning


def test_read_pipeline_downgrades_missing_risk_level(monkeypatch) -> None:
    result = _read(
        monkeypatch,
        _payload(stop_loss=None),
    )

    assert result.ctx is not None
    signal = result.ctx.signal
    assert signal.action == "watch"
    assert signal.entry is None
    assert signal.stop_loss is None
    assert signal.target is None
    assert "[INCOMPLETE SETUP" in signal.reasoning


def test_read_pipeline_downgrades_configured_symbol_mismatch(monkeypatch) -> None:
    result = _read(
        monkeypatch,
        _payload(symbol="NASDAQ:AAPL"),
        symbol_hint="NVDA",
    )

    assert result.ctx is not None
    signal = result.ctx.signal
    assert signal.symbol == "AAPL"
    assert signal.action == "watch"
    assert signal.entry is None
    assert signal.stop_loss is None
    assert signal.target is None
    assert "conflicts with configured symbol hint NVDA" in signal.reasoning



def test_valid_directional_signal_fails_closed_when_freshness_is_unknown(monkeypatch) -> None:
    result = _read(monkeypatch, _payload())

    assert result.ctx is not None
    signal = result.ctx.signal
    assert signal.action == "watch"
    assert signal.entry is None
    assert signal.stop_loss is None
    assert signal.target is None
    assert signal.position_size_pct is None
    assert signal.quantity is None
    assert "market-data freshness is explicitly unverified" in signal.reasoning


def test_vision_only_live_chart_evidence_allows_directional_signal(monkeypatch) -> None:
    result = _read(
        monkeypatch,
        _payload(chart_status="live", timestamp_visible=True, latest_candle_time="10:25"),
    )

    assert result.ctx is not None
    assert result.ctx.signal.action == "buy"
    assert result.ctx.signal.entry == 100.0
    assert result.ctx.signal.latest_candle_time == "10:25"


def test_vision_only_delayed_chart_is_downgraded(monkeypatch) -> None:
    result = _read(
        monkeypatch,
        _payload(chart_status="delayed", timestamp_visible=True, latest_candle_time="10:25"),
    )

    assert result.ctx is not None
    assert result.ctx.signal.action == "watch"
    assert result.ctx.signal.entry is None
    assert "freshness is explicitly unverified" in result.ctx.signal.reasoning


def test_valid_directional_signal_can_pass_with_verified_freshness_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_trader.cli._market_data_freshness",
        lambda cfg, signal: (5.0, True),
    )
    result = _read(monkeypatch, _payload())

    assert result.ctx is not None
    assert result.ctx.signal.action == "buy"
    assert result.ctx.signal.entry == 100.0
    assert result.ctx.signal.stop_loss == 98.0
    assert result.ctx.signal.target == 104.0


def test_stale_timestamp_metadata_downgrades_directional_signal(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_trader.cli._market_data_freshness",
        lambda cfg, signal: (61.0, True),
    )
    result = _read(monkeypatch, _payload())

    assert result.ctx is not None
    signal = result.ctx.signal
    assert signal.action == "watch"
    assert signal.entry is None
    assert signal.stop_loss is None
    assert signal.target is None
    assert "market data is stale (61s > 60s)" in signal.reasoning
