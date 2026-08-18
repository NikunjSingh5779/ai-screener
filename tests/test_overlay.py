"""Tests for the Phase 2 overlay display model (pure, offline) plus an
offscreen smoke test of the PyQt6 panel (skipped when PyQt6 isn't installed)."""

from __future__ import annotations

import os

import pytest

from ai_trader.overlay import HIGH_CONFIDENCE_THRESHOLD, SignalView, format_age, format_price, truncate
from ai_trader.signal import SignalContext, TradingSignal


def _ctx(**overrides: object) -> SignalContext:
    signal = TradingSignal(
        action=overrides.get("action", "buy"),  # type: ignore[arg-type]
        confidence=float(overrides.get("confidence", 72)),  # type: ignore[arg-type]
        entry=overrides.get("entry", 245.5),  # type: ignore[arg-type]
        stop_loss=overrides.get("stop_loss", 240.1),  # type: ignore[arg-type]
        target=overrides.get("target", 252.0),  # type: ignore[arg-type]
        position_size_pct=overrides.get("size", 4.5),  # type: ignore[arg-type]
        timeframe=overrides.get("timeframe", "15m"),  # type: ignore[arg-type]
        reasoning=overrides.get("reasoning", "breakout above resistance on volume"),  # type: ignore[arg-type]
        market=overrides.get("market", "NSE"),  # type: ignore[arg-type]
        symbol=overrides.get("symbol", "RELIANCE"),  # type: ignore[arg-type]
    )
    return SignalContext(
        symbol=signal.symbol,
        signal=signal,
        captured_at=float(overrides.get("captured_at", 1_000_000.0)),
        provider="",
        model=overrides.get("model", "google/gemma-4-31b-it:free"),  # type: ignore[arg-type]
    )


# --- format_price ----------------------------------------------------------


def test_format_price_none_is_missing() -> None:
    assert format_price(None) == "—"


def test_format_price_strips_trailing_zeros() -> None:
    assert format_price(245.0) == "245"
    assert format_price(245.5) == "245.5"


def test_format_price_rounds_to_two_decimals() -> None:
    assert format_price(1234.5678) == "1,234.57"


def test_format_price_small_values_keep_precision() -> None:
    assert format_price(0.0000123) == "0.0000123"


# --- format_age ------------------------------------------------------------


def test_format_age_buckets() -> None:
    assert format_age(0) == "0s"
    assert format_age(5) == "5s"
    assert format_age(65) == "1m 5s"
    assert format_age(3725) == "1h 2m"


# --- truncate --------------------------------------------------------------


def test_truncate_short_text_unchanged() -> None:
    assert truncate("hello") == "hello"


def test_truncate_long_text_gets_ellipsis() -> None:
    long_text = "word " * 60
    cut = truncate(long_text)
    assert len(cut) <= 140
    assert cut.endswith("…")


# --- SignalView ------------------------------------------------------------


def test_signal_view_from_context() -> None:
    view = SignalView.from_context(_ctx())
    assert view.action == "buy"
    assert view.action_label == "BUY"
    assert view.color.startswith("#")
    assert view.symbol == "RELIANCE"
    assert view.market == "NSE"
    assert view.confidence == "72%"
    assert view.entry == "245.5"
    assert view.stop_loss == "240.1"
    assert view.target == "252"
    assert view.size == "4.5%"
    assert view.timeframe == "15m"
    assert view.provider_model == "google/gemma-4-31b-it:free"


def test_signal_view_missing_levels() -> None:
    view = SignalView.from_context(
        _ctx(entry=None, stop_loss=None, target=None, size=None, symbol="", market="", confidence=50)
    )
    assert view.entry == "—"
    assert view.stop_loss == "—"
    assert view.target == "—"
    assert view.size == "—"
    assert view.symbol == "—"
    assert view.market == "—"
    assert view.confidence == "50%"


def test_signal_view_action_styles() -> None:
    for action, label in (("buy", "BUY"), ("sell", "SELL"), ("hold", "HOLD"), ("watch", "WATCH")):
        view = SignalView.from_context(_ctx(action=action, symbol="X"))
        assert view.action_label == label


def test_signal_view_age_and_stale() -> None:
    view = SignalView.from_context(_ctx(captured_at=1000.0))
    assert view.age_seconds(now=1005.0) == 5
    assert view.age_text(now=1005.0) == "5s"
    assert view.is_stale(now=1005.0) is False
    assert view.is_stale(now=1301.0) is True  # age 301s > default 300s stale window


def _offscreen_window():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
    from ai_trader.overlay_ui import OverlayWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return OverlayWindow, app


def test_signal_view_high_confidence_flag() -> None:
    """Task 3: the display model flags confidence at/above the threshold."""
    assert HIGH_CONFIDENCE_THRESHOLD == 80.0
    assert SignalView.from_context(_ctx(confidence=80.0)).is_high_confidence is True
    assert SignalView.from_context(_ctx(confidence=90.0)).is_high_confidence is True
    assert SignalView.from_context(_ctx(confidence=79.9)).is_high_confidence is False


# --- Qt smoke test (offscreen) --------------------------------------------


def test_overlay_window_renders_signal_offscreen() -> None:
    OverlayWindow, app = _offscreen_window()
    win = OverlayWindow()
    win.show_signal(SignalView.from_context(_ctx()))
    snapshot = win.snapshot()
    assert snapshot["action"] == "BUY"
    assert "RELIANCE" in snapshot["symbol_market"]
    assert snapshot["confidence"] == "72%"
    assert snapshot["entry"] == "ENTRY 245.5"
    assert snapshot["stop_loss"] == "SL 240.1"
    assert snapshot["target"] == "TARGET 252"
    assert snapshot["size"] == "SIZE 4.5%"
    assert "gemma" in snapshot["footer"]
    win.close()
    app.processEvents()


def test_overlay_window_renders_error() -> None:
    OverlayWindow, app = _offscreen_window()
    win = OverlayWindow()
    win.show_error("all providers failed")
    assert "all providers failed" in win.snapshot()["status"]
    win.close()
    app.processEvents()


def test_overlay_window_renders_quantity_offscreen() -> None:
    """Task 2 accept: the panel shows the risk-sized quantity when configured."""
    OverlayWindow, app = _offscreen_window()
    from ai_trader.config import load_config
    from ai_trader.cli import ReadResult

    signal = TradingSignal(
        action="buy",
        confidence=85.0,
        entry=245.5,
        stop_loss=240.1,
        target=252.0,
        position_size_pct=4.5,
        timeframe="15m",
        reasoning="breakout",
        market="NSE",
        symbol="RELIANCE",
    )
    ctx = SignalContext(
        symbol="RELIANCE",
        signal=signal,
        captured_at=1_000_000.0,
        provider="fake",
        model="fake/test",
    )

    cfg = load_config()
    cfg.account_size = 100_000.0
    cfg.risk_per_trade_pct = 1.0
    win = OverlayWindow(cfg=cfg)
    win._on_signal(ReadResult(ctx=ctx, elapsed=0.5))
    # 100000 × 1% = 1000 risked ÷ |245.5 − 240.1| = 5.4 → 185.185… qty
    assert win.snapshot()["qty"] == "QTY 185.185"


def test_overlay_window_zero_account_shows_missing_qty_offscreen() -> None:
    """Task 2 accept: unset/zero risk.account_size keeps QTY as em-dash, no crash."""
    OverlayWindow, app = _offscreen_window()
    from ai_trader.config import load_config
    from ai_trader.cli import ReadResult

    signal = TradingSignal(
        action="buy",
        confidence=85.0,
        entry=245.5,
        stop_loss=240.1,
        target=252.0,
        position_size_pct=4.5,
        timeframe="15m",
        reasoning="breakout",
        market="NSE",
        symbol="RELIANCE",
    )
    ctx = SignalContext(
        symbol="RELIANCE",
        signal=signal,
        captured_at=1_000_000.0,
        provider="fake",
        model="fake/test",
    )

    win = OverlayWindow(cfg=load_config())  # account_size 0.0 by default
    win._on_signal(ReadResult(ctx=ctx, elapsed=0.5))
    assert win.snapshot()["qty"] == "QTY —"


def test_overlay_window_high_confidence_visual_offscreen() -> None:
    """Task 3 accept: high-confidence directional signals show the badge."""
    OverlayWindow, app = _offscreen_window()
    from ai_trader.overlay import SignalView

    view = SignalView.from_context(_ctx(confidence=90.0))
    win = OverlayWindow()
    win.show_signal(view)
    assert win.snapshot()["high_conf"] == "⚡ HIGH CONFIDENCE"


def test_overlay_window_low_confidence_no_badge_offscreen() -> None:
    """Task 3 accept: sub-threshold confidence shows no badge."""
    OverlayWindow, app = _offscreen_window()
    from ai_trader.overlay import SignalView

    view = SignalView.from_context(_ctx(confidence=50.0))
    win = OverlayWindow()
    win.show_signal(view)
    assert win.snapshot()["high_conf"] == ""
