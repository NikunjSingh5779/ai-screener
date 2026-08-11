"""Trade Logger — logs trading signals to the Excel tracker.

Appends a row to the Trade_Log_Tracker.xlsx file when a trade is confirmed.
Uses openpyxl to load the workbook, find the first empty row, and write the values.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from ai_trader.signal import SignalContext

logger = logging.getLogger(__name__)

class LoggerError(RuntimeError):
    """Failed to log the trade (e.g. file is open/locked by another process)."""


class TradeLogger:
    """Logs confirmed trading signals to the Excel tracker."""

    def __init__(self, excel_path: str | Path = "Trade_Log_Tracker.xlsx", sheet_name: str = "Trade Log") -> None:
        self.excel_path = Path(excel_path)
        self.sheet_name = sheet_name

    def log_signal(self, ctx: SignalContext) -> None:
        """Append the signal as a new row in the tracker.
        
        Raises:
            LoggerError: if the file cannot be read or written (e.g. open in Excel).
        """
        try:
            import openpyxl
        except ImportError as exc:
            raise LoggerError("openpyxl is not installed (pip install openpyxl)") from exc

        if not self.excel_path.exists():
            raise LoggerError(f"Tracker file not found: {self.excel_path}")

        try:
            wb = openpyxl.load_workbook(self.excel_path)
        except Exception as exc:
            raise LoggerError(f"Failed to open workbook (is it open in Excel?): {exc}") from exc

        if self.sheet_name in wb.sheetnames:
            ws = wb[self.sheet_name]
        else:
            ws = wb.active

        dt = datetime.fromtimestamp(ctx.captured_at)
        signal = ctx.signal

        # Map to headers: ['Date', 'Time', 'Market', 'Symbol', 'Direction', 'Signal Source', 
        # 'AI Confidence %', 'Entry Price', 'Stop Loss', 'Target', 'Quantity', 'Currency', 
        # 'Capital Risked', 'Exit Price', 'Exit Time', 'P&L', 'P&L %', 'R Multiple', 'Status', 'Notes']
        
        row_data = [
            dt.strftime("%Y-%m-%d"),          # Date
            dt.strftime("%H:%M:%S"),          # Time
            signal.market,                    # Market
            ctx.symbol,                       # Symbol
            signal.action.upper(),            # Direction
            ctx.model,                        # Signal Source
            signal.confidence,                # AI Confidence %
            signal.entry,                     # Entry Price
            signal.stop_loss,                 # Stop Loss
            signal.target,                    # Target
            None,                             # Quantity
            None,                             # Currency
            signal.position_size_pct,         # Capital Risked (using position size)
            None,                             # Exit Price
            None,                             # Exit Time
            None,                             # P&L
            None,                             # P&L %
            None,                             # R Multiple
            "Open",                           # Status
            signal.reasoning                  # Notes
        ]

        try:
            ws.append(row_data)
            wb.save(self.excel_path)
        except Exception as exc:
            raise LoggerError(f"Failed to save workbook (is it open in Excel?): {exc}") from exc

        logger.info("Trade logged to %s for %s (%s)", self.excel_path.name, ctx.symbol, signal.action)
