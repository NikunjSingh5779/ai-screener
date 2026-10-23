"""Tests for explicitly confirmed, manual trade lifecycle tracking."""

from __future__ import annotations

import csv
from types import SimpleNamespace

import pytest

from ai_trader.trade_tracker import ManualTradeError, ManualTradeTracker


def make_context(action: str = "buy", model: str = "local/test"):
    signal = SimpleNamespace(
        action=action,
        symbol="RELIANCE",
        market="NSE",
        timeframe="5m",
        confidence=82.0,
        entry=100.0,
        stop_loss=95.0 if action == "buy" else 105.0,
        target=110.0 if action == "buy" else 90.0,
    )
    return SimpleNamespace(
        signal=signal,
        symbol=signal.symbol,
        captured_at=1_700_000_000.0,
        model=model,
    )


def test_entry_persists_active_trade_across_restart(tmp_path) -> None:
    state = tmp_path / "state.json"
    journal = tmp_path / "journal.csv"
    tracker = ManualTradeTracker(state, journal)

    active = tracker.open_trade(make_context(), actual_entry=100.25, quantity=10, entered_at=1_700_000_100.0)

    assert active["actual_entry"] == 100.25
    assert active["quantity"] == 10
    assert tracker.active_trade["symbol"] == "RELIANCE"
    reloaded = ManualTradeTracker(state, journal)
    assert reloaded.active_trade["side"] == "buy"
    assert not journal.exists()


def test_close_trade_logs_gross_pnl_r_multiple_and_holding_time(tmp_path) -> None:
    tracker = ManualTradeTracker(tmp_path / "state.json", tmp_path / "journal.csv")
    tracker.open_trade(make_context(), actual_entry=100.0, quantity=10, entered_at=1_700_000_100.0)

    closed = tracker.close_trade(actual_exit=110.0, exited_at=1_700_000_400.0)

    assert closed["gross_pnl_before_costs"] == 100.0
    assert closed["initial_risk_amount"] == 50.0
    assert closed["realized_r_multiple"] == 2.0
    assert closed["holding_seconds"] == 300.0
    assert tracker.active_trade is None
    with (tmp_path / "journal.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "RELIANCE"
    assert rows[0]["gross_pnl_before_costs"] == "100.0"


def test_short_trade_pnl_is_directionally_correct(tmp_path) -> None:
    tracker = ManualTradeTracker(tmp_path / "state.json", tmp_path / "journal.csv")
    tracker.open_trade(make_context("sell"), actual_entry=100.0, quantity=5, entered_at=1_700_000_100.0)
    closed = tracker.close_trade(actual_exit=90.0, exited_at=1_700_000_200.0)
    assert closed["gross_pnl_before_costs"] == 50.0


def test_loss_is_negative_and_costs_are_not_claimed_included(tmp_path) -> None:
    tracker = ManualTradeTracker(tmp_path / "state.json", tmp_path / "journal.csv")
    tracker.open_trade(make_context(), actual_entry=100.0, quantity=2, entered_at=1_700_000_100.0)
    closed = tracker.close_trade(actual_exit=98.0, exited_at=1_700_000_200.0)
    assert closed["gross_pnl_before_costs"] == -4.0


def test_rejects_mock_and_duplicate_active_trade(tmp_path) -> None:
    tracker = ManualTradeTracker(tmp_path / "state.json", tmp_path / "journal.csv")
    with pytest.raises(ManualTradeError, match="Mock/test"):
        tracker.open_trade(make_context(model="noop/mock"), 100, 1)
    tracker.open_trade(make_context(), 100, 1, entered_at=1_700_000_100.0)
    with pytest.raises(ManualTradeError, match="already active"):
        tracker.open_trade(make_context(), 100, 1, entered_at=1_700_000_101.0)


@pytest.mark.parametrize(
    ("entry", "quantity"),
    [(0, 1), (-10, 1), (float("nan"), 1), (100, 0), (100, 1.5)],
)
def test_rejects_invalid_entry_and_quantity(tmp_path, entry, quantity) -> None:
    tracker = ManualTradeTracker(tmp_path / "state.json", tmp_path / "journal.csv")
    with pytest.raises(ManualTradeError):
        tracker.open_trade(make_context(), entry, quantity, entered_at=1_700_000_100.0)


def test_close_requires_active_trade_and_positive_exit(tmp_path) -> None:
    tracker = ManualTradeTracker(tmp_path / "state.json", tmp_path / "journal.csv")
    with pytest.raises(ManualTradeError, match="no active"):
        tracker.close_trade(100)
    tracker.open_trade(make_context(), 100, 1, entered_at=1_700_000_100.0)
    with pytest.raises(ManualTradeError, match="positive"):
        tracker.close_trade(0, exited_at=1_700_000_200.0)
    with pytest.raises(ManualTradeError, match="precede"):
        tracker.close_trade(101, exited_at=1_700_000_099.0)
    with pytest.raises(ManualTradeError, match="timestamp"):
        tracker.close_trade(101, exited_at=float("nan"))
    assert tracker.active_trade is not None


def test_rejects_non_directional_signal(tmp_path) -> None:
    tracker = ManualTradeTracker(tmp_path / "state.json", tmp_path / "journal.csv")
    with pytest.raises(ManualTradeError, match="BUY or SELL"):
        tracker.open_trade(make_context("hold"), 100, 1, entered_at=1_700_000_100.0)
