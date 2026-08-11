"""Tests for the Rate & Cost Guard: sliding-window budget, min interval, reset."""

from __future__ import annotations

from ai_trader.rate_guard import RateGuard


def test_budget_enforces_calls_per_minute() -> None:
    guard = RateGuard(calls_per_minute=3, min_interval_seconds=0)
    assert guard.should_allow("p", now=100.0) is True
    assert guard.should_allow("p", now=100.5) is True
    assert guard.should_allow("p", now=101.0) is True
    assert guard.should_allow("p", now=101.5) is False  # window exhausted
    assert guard.remaining("p", now=101.5) == 0


def test_window_slides_out() -> None:
    guard = RateGuard(calls_per_minute=1, min_interval_seconds=0)
    assert guard.should_allow("p", now=0.0) is True
    assert guard.should_allow("p", now=30.0) is False
    assert guard.should_allow("p", now=61.0) is True   # old call aged out of the window
    assert guard.remaining("p", now=61.0) == 0


def test_min_interval_blocks_rapid_calls() -> None:
    guard = RateGuard(calls_per_minute=100, min_interval_seconds=5.0)
    assert guard.should_allow("p", now=0.0) is True
    assert guard.should_allow("p", now=4.9) is False
    assert guard.should_allow("p", now=5.0) is True


def test_remaining_counts_slots() -> None:
    guard = RateGuard(calls_per_minute=2, min_interval_seconds=0)
    guard.should_allow("p", now=0.0)
    assert guard.remaining("p", now=1.0) == 1


def test_providers_are_independent() -> None:
    guard = RateGuard(calls_per_minute=1, min_interval_seconds=0)
    assert guard.should_allow("a", now=0.0) is True
    assert guard.should_allow("b", now=0.1) is True  # b unaffected by a's usage
    assert guard.should_allow("a", now=0.2) is False


def test_reset_clears_one_provider() -> None:
    guard = RateGuard(calls_per_minute=1, min_interval_seconds=0)
    guard.should_allow("p", now=0.0)
    guard.reset("p")
    assert guard.remaining("p", now=1.0) == 1
    assert guard.should_allow("p", now=1.0) is True


def test_invalid_budget_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        RateGuard(calls_per_minute=0)
