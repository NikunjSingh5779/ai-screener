"""Manual trade lifecycle tracking for the advisory-only floating panel.

This module records only trades the user explicitly confirms they entered/exited.
It never sends orders to a broker. Closed-trade P&L is gross, before fees/slippage.
"""

from __future__ import annotations

import csv
import json
import math
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


class ManualTradeError(RuntimeError):
    """Invalid manual trade action or an unreadable persistence file."""


class ManualTradeTracker:
    """Persist one manually-confirmed active trade and append closed trades to CSV."""

    JOURNAL_FIELDS = [
        "trade_id",
        "symbol",
        "market",
        "currency",
        "side",
        "timeframe",
        "confidence",
        "model",
        "signal_time",
        "signal_entry",
        "stop_loss",
        "target",
        "actual_entry",
        "quantity",
        "entered_at",
        "actual_exit",
        "exited_at",
        "holding_seconds",
        "gross_pnl_before_costs",
        "initial_risk_amount",
        "realized_r_multiple",
    ]

    def __init__(
        self,
        state_path: str | Path = "Manual_Trade_State.json",
        journal_path: str | Path = "Manual_Trade_Journal.csv",
    ) -> None:
        self.state_path = Path(state_path)
        self.journal_path = Path(journal_path)
        self._active = self._load_active()

    @property
    def active_trade(self) -> dict[str, Any] | None:
        return dict(self._active) if self._active is not None else None

    def _load_active(self) -> dict[str, Any] | None:
        if not self.state_path.exists():
            return None
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            if raw is None:
                return None
            if not isinstance(raw, dict):
                raise ValueError("state root must be an object or null")
            required = {"symbol", "market", "side", "actual_entry", "quantity", "entered_at"}
            if not required.issubset(raw):
                raise ValueError("active trade is missing required fields")
            return raw
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ManualTradeError(f"Cannot load manual trade state {self.state_path}: {exc}") from exc

    @staticmethod
    def _finite_positive(value: float, name: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ManualTradeError(f"{name} must be a positive number") from exc
        if not math.isfinite(number) or number <= 0:
            raise ManualTradeError(f"{name} must be a finite positive number")
        return number

    @staticmethod
    def _iso_time(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")

    def _save_state(self, trade: dict[str, Any] | None) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        try:
            temp_path.write_text(json.dumps(trade, indent=2), encoding="utf-8")
            temp_path.replace(self.state_path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ManualTradeError(f"Cannot save manual trade state: {exc}") from exc

    def open_trade(
        self,
        ctx: Any,
        actual_entry: float,
        quantity: float,
        entered_at: float | None = None,
    ) -> dict[str, Any]:
        """Record a user-confirmed fill. Refuses mock signals and duplicate opens."""
        if self._active is not None:
            raise ManualTradeError("A manual trade is already active; close it before recording another.")
        signal = ctx.signal
        side = str(signal.action).lower()
        if side not in {"buy", "sell"}:
            raise ManualTradeError("Only BUY or SELL signals can start a tracked trade.")
        if str(getattr(ctx, "model", "")).startswith("noop/"):
            raise ManualTradeError("Mock/test output cannot be used to start a tracked trade.")
        price = self._finite_positive(actual_entry, "Actual entry price")
        market = str(signal.market or "").strip().upper()
        if market not in {"NSE", "BSE", "US"}:
            raise ManualTradeError("Market must be confidently identified as NSE, BSE or US.")
        try:
            qty = float(quantity)
        except (TypeError, ValueError) as exc:
            raise ManualTradeError("Quantity must be a positive number of shares.") from exc
        if not math.isfinite(qty) or qty <= 0:
            raise ManualTradeError("Quantity must be a finite positive number.")
        if market in {"NSE", "BSE"} and not qty.is_integer():
            raise ManualTradeError("Indian cash-equity share quantity must be a whole number.")

        try:
            timestamp = float(entered_at if entered_at is not None else datetime.now().timestamp())
        except (TypeError, ValueError) as exc:
            raise ManualTradeError("Entry timestamp is invalid.") from exc
        if not math.isfinite(timestamp) or timestamp <= 0:
            raise ManualTradeError("Entry timestamp is invalid.")

        trade = {
            "trade_id": uuid.uuid4().hex,
            "symbol": str(signal.symbol or ctx.symbol or "").upper(),
            "market": market,
            "currency": "USD" if market == "US" else "INR",
            "side": side,
            "timeframe": str(signal.timeframe or ""),
            "confidence": float(signal.confidence),
            "model": str(ctx.model or ""),
            "signal_time": self._iso_time(float(ctx.captured_at)),
            "signal_entry": signal.entry,
            "stop_loss": signal.stop_loss,
            "target": signal.target,
            "actual_entry": price,
            "quantity": qty,
            "entered_at": timestamp,
            "entered_at_local": self._iso_time(timestamp),
        }
        if not trade["symbol"]:
            raise ManualTradeError("Signal symbol is missing; cannot track this trade safely.")
        self._save_state(trade)
        self._active = trade
        return dict(trade)

    def close_trade(
        self,
        actual_exit: float,
        exited_at: float | None = None,
    ) -> dict[str, Any]:
        """Record a user-confirmed exit and append the completed trade to CSV."""
        if self._active is None:
            raise ManualTradeError("There is no active manual trade to close.")
        exit_price = self._finite_positive(actual_exit, "Actual exit price")
        try:
            timestamp = float(exited_at if exited_at is not None else datetime.now().timestamp())
        except (TypeError, ValueError) as exc:
            raise ManualTradeError("Exit timestamp is invalid.") from exc
        if not math.isfinite(timestamp) or timestamp < float(self._active["entered_at"]):
            raise ManualTradeError("Exit timestamp cannot precede entry timestamp.")

        trade = dict(self._active)
        quantity = float(trade["quantity"])
        entry_price = float(trade["actual_entry"])
        side = str(trade["side"])
        gross_pnl = (exit_price - entry_price) * quantity
        if side == "sell":
            gross_pnl = -gross_pnl

        stop = trade.get("stop_loss")
        try:
            unit_risk = abs(entry_price - float(stop)) if stop is not None else 0.0
        except (TypeError, ValueError):
            unit_risk = 0.0
        initial_risk = unit_risk * quantity
        realized_r = gross_pnl / initial_risk if initial_risk > 0 else None

        closed = {
            **trade,
            "actual_exit": exit_price,
            "exited_at": self._iso_time(timestamp),
            "holding_seconds": round(max(0.0, timestamp - float(trade["entered_at"])), 2),
            "gross_pnl_before_costs": round(gross_pnl, 4),
            "initial_risk_amount": round(initial_risk, 4) if initial_risk > 0 else None,
            "realized_r_multiple": round(realized_r, 4) if realized_r is not None else None,
        }
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        file_exists = self.journal_path.exists() and self.journal_path.stat().st_size > 0
        try:
            # If the CSV append succeeded but clearing persisted state failed,
            # a retry must not create a duplicate completed-trade row.
            already_logged = False
            if file_exists:
                with self.journal_path.open("r", newline="", encoding="utf-8") as handle:
                    already_logged = any(
                        row.get("trade_id") == trade.get("trade_id")
                        for row in csv.DictReader(handle)
                    )
            if not already_logged:
                with self.journal_path.open("a", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=self.JOURNAL_FIELDS, extrasaction="ignore")
                    if not file_exists:
                        writer.writeheader()
                    writer.writerow(closed)
        except OSError as exc:
            raise ManualTradeError(f"Cannot write manual trade journal: {exc}") from exc

        self._save_state(None)
        self._active = None
        return closed
