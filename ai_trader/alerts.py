"""High-confidence directional alerts.

A directional (buy/sell) signal at or above a confidence threshold gets a
short system beep. The beep is injected so tests can substitute a mock and the
caller never depends on ``winsound`` being present (it is a silent no-op on
platforms without it, matching how ``keyboard`` is treated as optional).

Advisory only: this beeps to notify; it never executes a trade.
"""

from __future__ import annotations

from typing import Callable

from ai_trader.signal import TradingSignal


def should_alert(signal: TradingSignal, threshold: float) -> bool:
    """True only for a directional (buy/sell) signal at/above ``threshold``."""
    return signal.action in ("buy", "sell") and signal.confidence >= threshold


def beep() -> None:
    """A short high-pitched system beep. Silent no-op where unavailable."""
    try:
        import winsound  # Windows only; raises ImportError elsewhere
    except ImportError:  # pragma: no cover - platform dependent
        return
    try:
        winsound.Beep(880, 200)
    except Exception:  # pragma: no cover - e.g. no sound device
        pass


def alert(
    signal: TradingSignal,
    threshold: float,
    beep_fn: Callable[[], None] = beep,
) -> bool:
    """Beep once for a qualifying high-confidence directional signal.

    Returns ``True`` when an alert was raised. Injection of ``beep_fn`` lets
    tests substitute a mock instead of actually calling ``winsound``.
    """
    if should_alert(signal, threshold):
        beep_fn()
        return True
    return False