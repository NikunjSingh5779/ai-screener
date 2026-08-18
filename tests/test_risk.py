"""Tests for risk-based position sizing (Task 2)."""

from __future__ import annotations

import pytest

from ai_trader.risk import compute_position_size


def test_compute_position_size_normal_case() -> None:
    """Account 100000, risk 1%, entry 100, stop 99 -> 1000.0 units."""
    qty = compute_position_size(100000.0, 1.0, 100.0, 99.0)
    assert qty == 1000.0


def test_compute_position_size_entry_equals_stop() -> None:
    """entry == stop -> None (division by zero)."""
    qty = compute_position_size(100000.0, 1.0, 100.0, 100.0)
    assert qty is None


def test_compute_position_size_missing_entry() -> None:
    """entry is None -> None."""
    qty = compute_position_size(100000.0, 1.0, None, 99.0)
    assert qty is None


def test_compute_position_size_missing_stop_loss() -> None:
    """stop_loss is None -> None."""
    qty = compute_position_size(100000.0, 1.0, 100.0, None)
    assert qty is None


def test_compute_position_size_account_size_zero() -> None:
    """account_size <= 0 -> None (nothing to size against)."""
    qty = compute_position_size(0.0, 1.0, 100.0, 99.0)
    assert qty is None


def test_compute_position_size_account_size_negative() -> None:
    """account_size < 0 -> None."""
    qty = compute_position_size(-1000.0, 1.0, 100.0, 99.0)
    assert qty is None


def test_compute_position_size_crypto_small_values() -> None:
    """Crypto levels: small entry/stop differences still work."""
    qty = compute_position_size(10000.0, 2.0, 0.00001, 0.000005)
    # risk_amount = 200, per_unit_risk = 0.000005 -> 40,000,000
    assert qty == 40000000.0