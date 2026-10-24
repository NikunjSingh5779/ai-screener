"""Tests for risk-based position sizing (Task 2)."""

from __future__ import annotations

import math

from ai_trader.risk import compute_position_size


def test_compute_position_size_normal_case() -> None:
    assert compute_position_size(100000.0, 1.0, 100.0, 99.0) == 1000.0


def test_compute_position_size_entry_equals_stop() -> None:
    assert compute_position_size(100000.0, 1.0, 100.0, 100.0) is None


def test_compute_position_size_missing_levels() -> None:
    assert compute_position_size(100000.0, 1.0, None, 99.0) is None
    assert compute_position_size(100000.0, 1.0, 100.0, None) is None


def test_compute_position_size_account_size_invalid() -> None:
    assert compute_position_size(0.0, 1.0, 100.0, 99.0) is None
    assert compute_position_size(-1000.0, 1.0, 100.0, 99.0) is None
    assert compute_position_size(math.inf, 1.0, 100.0, 99.0) is None


def test_compute_position_size_risk_percentage_invalid() -> None:
    assert compute_position_size(100000.0, 0.0, 100.0, 99.0) is None
    assert compute_position_size(100000.0, -1.0, 100.0, 99.0) is None
    assert compute_position_size(100000.0, 101.0, 100.0, 99.0) is None
    assert compute_position_size(100000.0, math.nan, 100.0, 99.0) is None


def test_compute_position_size_prices_must_be_finite() -> None:
    assert compute_position_size(100000.0, 1.0, math.inf, 99.0) is None
    assert compute_position_size(100000.0, 1.0, 100.0, math.nan) is None


def test_compute_position_size_crypto_small_values() -> None:
    assert compute_position_size(10000.0, 2.0, 0.00001, 0.000005) == 40000000.0
