"""Order-ticket price helpers for manual TradingView entry preparation.

These functions only calculate values to review/copy into the charting platform.
They never place, submit, modify or cancel orders.
"""
from __future__ import annotations
import math

TAKE_PROFIT_MODES = ("Price", "Ticks", "% price", "Reward USD", "Reward % balance")
STOP_LOSS_MODES = ("Price", "Ticks", "% price", "Risk USD", "Risk % balance")
ORDER_TYPES = ("Market", "Limit", "Stop")


class OrderTicketError(ValueError):
    """Invalid inputs for an advisory manual order ticket."""


def _positive(value: float, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise OrderTicketError(f"{label} must be numeric.") from exc
    if not math.isfinite(number) or number <= 0:
        raise OrderTicketError(f"{label} must be finite and positive.")
    return number


def calculate_exit_price(
    *,
    side: str,
    role: str,
    mode: str,
    value: float,
    entry_price: float,
    quantity: int,
    tick_size: float = 0.01,
    account_balance: float | None = None,
) -> float:
    """Convert a take-profit/stop-loss setting into an absolute price.

    Reward/Risk amount modes refer to gross price movement multiplied by
    quantity. The user must still account for fees, slippage, lot sizes and
    instrument-specific rules. This function never submits an order.
    """
    side = str(side).strip().lower()
    role = str(role).strip().lower()
    mode = str(mode).strip()
    if side not in {"buy", "sell"}:
        raise OrderTicketError("Side must be BUY or SELL.")
    if role not in {"take_profit", "stop_loss"}:
        raise OrderTicketError("Role must be take_profit or stop_loss.")
    modes = TAKE_PROFIT_MODES if role == "take_profit" else STOP_LOSS_MODES
    if mode not in modes:
        raise OrderTicketError(f"Unsupported exit mode: {mode!r}.")

    entry = _positive(entry_price, "Entry price")
    amount = _positive(value, "Exit setting")
    try:
        qty = int(quantity)
    except (TypeError, ValueError) as exc:
        raise OrderTicketError("Quantity must be a positive whole number.") from exc
    if qty <= 0 or float(quantity) != qty:
        raise OrderTicketError("Quantity must be a positive whole number.")

    # Positive direction means the target is above entry; negative below.
    direction = 1.0 if side == "buy" else -1.0
    if role == "stop_loss":
        direction *= -1.0

    if mode == "Price":
        result = amount
    else:
        if mode == "Ticks":
            distance = amount * _positive(tick_size, "Tick size")
        elif mode == "% price":
            distance = entry * amount / 100.0
        elif mode in {"Reward USD", "Risk USD"}:
            distance = amount / qty
        elif mode in {"Reward % balance", "Risk % balance"}:
            balance = _positive(account_balance or 0, "Account balance")
            if amount > 100:
                raise OrderTicketError("Balance percentage must be between 0 and 100.")
            distance = balance * amount / 100.0 / qty
        else:
            raise OrderTicketError(f"Unsupported exit mode: {mode!r}.")
        result = entry + direction * distance

    if not math.isfinite(result) or result <= 0:
        raise OrderTicketError("Calculated exit price is not a valid positive price.")

    if role == "take_profit":
        valid = result > entry if side == "buy" else result < entry
    else:
        valid = result < entry if side == "buy" else result > entry
    if not valid:
        position = "above" if (role == "take_profit" and side == "buy") or (role == "stop_loss" and side == "sell") else "below"
        raise OrderTicketError(f"{role.replace('_', ' ').title()} price must be {position} the entry.")
    return round(result, 8)


def calculate_risk_reward(entry_price: float, stop_loss_price: float, target_price: float) -> float:
    """Return absolute reward/risk ratio; prices must be distinct."""
    entry = _positive(entry_price, "Entry price")
    stop = _positive(stop_loss_price, "Stop-loss price")
    target = _positive(target_price, "Target price")
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0 or reward <= 0:
        raise OrderTicketError("Entry, stop-loss and target must be distinct prices.")
    return reward / risk
