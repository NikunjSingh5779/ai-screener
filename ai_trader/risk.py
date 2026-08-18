"""Risk-based position sizing.

Grounds the vision model's ``position_size_pct`` guess in real risk math:
how many units to hold so that a stop-out risks ``risk_per_trade_pct``% of the
account. The vision model is unreliable at precise arithmetic, so the raw
``position_size_pct`` stays the model's own label while ``quantity`` is
computed here from the user's account/risk settings.

Advisory only: this computes a suggested quantity; it never places or confirms
a trade.
"""

from __future__ import annotations


def compute_position_size(
    account_size: float,
    risk_per_trade_pct: float,
    entry: float | None,
    stop_loss: float | None,
) -> float | None:
    """Units to hold so that a stop-out risks ~risk_per_trade_pct% of account_size.

    Returns ``None`` if ``entry``/``stop_loss`` are missing, equal, or when
    ``account_size <= 0`` (nothing to size against).
    """
    if account_size <= 0 or entry is None or stop_loss is None:
        return None
    per_unit_risk = abs(entry - stop_loss)
    if per_unit_risk <= 0:
        return None
    risk_amount = account_size * risk_per_trade_pct / 100
    return risk_amount / per_unit_risk