"""Phase 2 Overlay display model — pure logic, no Qt dependency.

Turns a :class:`~ai_trader.signal.SignalContext` into the text/color a floating
panel needs to render: action label, confidence, entry/stop/target/size, the
signal's age ("signal is 8s old", plan §2.5), and a stale flag so an old call
visibly degrades instead of silently persisting.

Keeping this module Qt-free means the whole display model is unit-testable
offline, and the overlay window (``overlay_ui.py``) stays a thin renderer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle avoided; runtime never needs it
    from ai_trader.signal import SignalContext

#: Display style per action — the panel renders the *label* and *color* so a
#: buy call is visually unambiguous even to a glance.
ACTION_STYLES: dict[str, dict[str, str]] = {
    "buy": {"label": "BUY", "color": "#22c55e"},
    "sell": {"label": "SELL", "color": "#ef4444"},
    "hold": {"label": "HOLD", "color": "#f59e0b"},
    "watch": {"label": "WATCH", "color": "#94a3b8"},
}

#: An em-dash stands in for any level the model could not see on the chart.
MISSING = "—"

#: A signal older than this is flagged stale on the panel (plan §6: treat
#: timing as stale rather than acting on it late).
DEFAULT_STALE_AFTER = 300.0


def format_price(value: float | None) -> str:
    """Compact, locale-stable price formatting.

    Trailing zeros are stripped (``245.0 -> "245"``); small values (crypto
    levels) keep more decimals instead of collapsing to ``"0"``.
    """
    if value is None:
        return MISSING
    if value == 0:
        return "0"
    if abs(value) < 1:
        text = f"{value:.8f}".rstrip("0").rstrip(".")
    else:
        text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text


def format_qty(value: float | None) -> str:
    """Format the risk-computed quantity (whole units for equities)."""
    if value is None:
        return MISSING
    return f"{value:g}"


def format_age(seconds: float) -> str:
    """Human age string: ``"5s"``, ``"1m 5s"``, ``"1h 2m"``."""
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def truncate(text: str, limit: int = 140) -> str:
    """Flatten whitespace and cut to ``limit`` chars with an ellipsis."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class SignalView:
    """Everything the overlay window needs to render one signal, pre-formatted."""

    action: str
    action_label: str
    color: str
    symbol: str
    market: str
    confidence: str
    entry: str
    stop_loss: str
    target: str
    size: str
    qty: str
    timeframe: str
    reasoning: str
    provider_model: str
    captured_at: float
    stale_after: float = DEFAULT_STALE_AFTER

    def age_seconds(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        return max(0.0, now - self.captured_at)

    def age_text(self, now: float | None = None) -> str:
        return format_age(self.age_seconds(now))

    def is_stale(self, now: float | None = None) -> bool:
        return self.age_seconds(now) > self.stale_after

    @classmethod
    def from_context(cls, ctx: "SignalContext") -> "SignalView":
        """Build a renderable view from a parsed signal (plus capture metadata)."""
        signal = ctx.signal
        style = ACTION_STYLES.get(signal.action.lower(), ACTION_STYLES["watch"])
        size = (
            f"{format_price(signal.position_size_pct)}%"
            if signal.position_size_pct is not None
            else MISSING
        )
        qty = format_qty(signal.quantity)
        return cls(
            action=signal.action.lower(),
            action_label=style["label"],
            color=style["color"],
            symbol=(signal.symbol or MISSING).upper(),
            market=signal.market or MISSING,
            confidence=f"{signal.confidence:.0f}%",
            entry=format_price(signal.entry),
            stop_loss=format_price(signal.stop_loss),
            target=format_price(signal.target),
            size=size,
            qty=qty,
            timeframe=signal.timeframe or MISSING,
            reasoning=truncate(signal.reasoning),
            provider_model=ctx.model or MISSING,
            captured_at=ctx.captured_at,
        )
