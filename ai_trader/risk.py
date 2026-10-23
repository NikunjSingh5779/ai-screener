"""Risk-based position sizing.

This produces an advisory quantity only; it never executes an order.
"""

from __future__ import annotations

import math


def compute_position_size(
    account_size: float,
    risk_per_trade_pct: float,
    entry: float | None,
    stop_loss: float | None,
) -> float | None:
    """Return units whose stop-out risks the configured account percentage.

    Reject invalid/non-finite values and out-of-range risk percentages rather
    than propagating NaN/inf into the overlay or the Excel log.
    """
    values = (account_size, risk_per_trade_pct)
    if not all(math.isfinite(float(value)) for value in values):
        return None
    if account_size <= 0 or not 0 < risk_per_trade_pct <= 100:
        return None
    if entry is None or stop_loss is None:
        return None
    if not math.isfinite(float(entry)) or not math.isfinite(float(stop_loss)):
        return None
    per_unit_risk = abs(float(entry) - float(stop_loss))
    if per_unit_risk <= 0:
        return None
    quantity = (float(account_size) * float(risk_per_trade_pct) / 100.0) / per_unit_risk
    return quantity if math.isfinite(quantity) and quantity > 0 else None
