"""Phase 2 Overlay UI — the always-on-top PyQt6 panel (plan §2.5).

Shows the latest trading call — action, confidence, entry/stop/target/size, the
signal's age ("signal is 8s old"), and the provider/model that served it.

Design notes:
  * Frameless + ``WindowStaysOnTopHint`` + ``Tool`` so it floats over the
    trading platform and never appears in the taskbar.
  * ``WA_ShowWithoutActivating`` — the panel never steals keyboard focus from
    the platform (a hallucinated call must never interrupt what you're doing).
  * Reads run on a worker thread (hotkey or menu-triggered); results reach the
    panel through queued signals, so a multi-second vision-API call never
    freezes the age ticker.
  * Left-drag to reposition; right-click for "Read now" / "Quit"; ✕ to close.
"""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import replace
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ai_trader.config import Config, account_size_for_market, load_config
from ai_trader.overlay import SignalView, format_age
from ai_trader.logger import TradeLogger
from ai_trader.alerts import alert
from ai_trader.risk import compute_position_size
from ai_trader.trade_tracker import ManualTradeError, ManualTradeTracker
from ai_trader.rate_guard import minimum_safe_poll_interval_seconds

if TYPE_CHECKING:  # pragma: no cover - type-only; cli is lazily imported at runtime
    from ai_trader.cli import ReadResult
    from ai_trader.signal import SignalContext

logger = logging.getLogger(__name__)

# Panel palette.
PANEL_BG = "rgba(17, 20, 28, 235)"
BORDER = "rgba(255, 255, 255, 32)"
HIGH_BORDER = "rgba(245, 158, 11, 200)"  # high-confidence state: amber border
HIGH_CONFIDENCE_COLOR = "#f59e0b"  # badge text: amber
TEXT = "#e2e8f0"
DIM = "#94a3b8"
STALE = "#ef4444"
ERROR = "#f87171"

PANEL_WIDTH = 320
SCREEN_MARGIN = 16
FONT_SMALL = "font-size:11px;"
FONT_TINY = "font-size:9px;"


class OverlayWindow(QWidget):
    """Floating always-on-top panel that renders the latest :class:`SignalView`."""

    #: Carries a ``ReadResult`` from the read worker thread to the GUI thread.
    signal_ready = pyqtSignal(object)
    #: Request a read (emitted by the right-click "Read now" action).
    read_requested = pyqtSignal()
    #: Request trade logging (hotkey or menu). Marshalled to GUI thread.
    log_requested = pyqtSignal()
    #: Toggle periodic screen monitoring from the right-click menu.
    watch_toggle_requested = pyqtSignal()
    #: Fired when the window is closed (run_overlay uses it to quit the app).
    closed = pyqtSignal()
    #: Transient status text (e.g. "Reading…") marshalled to the GUI thread.
    status_changed = pyqtSignal(str)
    #: An unexpected error surfaced outside :func:`read_signal`.
    error_reported = pyqtSignal(str)

    def __init__(
        self,
        trade_logger: TradeLogger | None = None,
        cfg: Config | None = None,
        manual_tracker: ManualTradeTracker | None = None,
        watch_enabled: bool = False,
    ) -> None:
        super().__init__()
        self._watch_enabled = watch_enabled
        self._view: SignalView | None = None
        self._ctx: "SignalContext" | None = None
        self._drag_offset: tuple[float, float] | None = None
        self._trade_logger = trade_logger
        self._cfg = cfg
        self._manual_tracker = manual_tracker or ManualTradeTracker()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("AI Trader")

        self._build_ui()
        self.status_changed.connect(self.set_status)
        self.error_reported.connect(self.show_error)
        self.signal_ready.connect(self._on_signal)
        self.log_requested.connect(self.log_current_signal)

        self._position()
        self._refresh_trade_controls()

        # Tick the age line once a second (plan §2.5: "signal is 8s old").
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._update_age)
        self._clock.start(1000)

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("card")
        self._card = card
        card.setStyleSheet(
            f"#card {{ background-color: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 10px; }}"
        )
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(5)

        top = QHBoxLayout()
        title = QLabel("AI TRADER")
        title.setStyleSheet(f"color:{DIM}; {FONT_TINY} font-weight:600; letter-spacing:1px;")
        top.addWidget(title)
        top.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFlat(True)
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"color:{DIM}; font-size:12px; background:transparent;")
        close_btn.clicked.connect(self.close)
        top.addWidget(close_btn)
        layout.addLayout(top)

        header = QHBoxLayout()
        self._action = QLabel("—")
        self._action.setStyleSheet("font-size:20px; font-weight:800;")
        header.addWidget(self._action)
        header.addSpacing(8)
        self._symbol = QLabel("")
        self._symbol.setStyleSheet(f"color:{TEXT}; font-size:14px; font-weight:600;")
        header.addWidget(self._symbol)
        header.addStretch()
        self._confidence = QLabel("")
        self._confidence.setStyleSheet(f"color:{TEXT}; font-size:14px; font-weight:600;")
        header.addWidget(self._confidence)
        layout.addLayout(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(2)
        self._entry = self._level_label()
        self._sl = self._level_label()
        self._target = self._level_label()
        self._size = self._level_label()
        self._qty = self._level_label()
        grid.addWidget(self._entry, 0, 0)
        grid.addWidget(self._sl, 0, 1)
        grid.addWidget(self._target, 1, 0)
        grid.addWidget(self._size, 1, 1)
        grid.addWidget(self._qty, 2, 0, 1, 2)
        layout.addLayout(grid)

        self._timeframe = QLabel("")
        self._timeframe.setStyleSheet(f"color:{DIM}; {FONT_SMALL}")
        layout.addWidget(self._timeframe)

        self._outlook = QLabel("OUTLOOK · UNCLEAR")
        self._outlook.setStyleSheet(f"color:{HIGH_CONFIDENCE_COLOR}; {FONT_SMALL} font-weight:700;")
        self._outlook.setWordWrap(True)
        layout.addWidget(self._outlook)

        self._outlook_reasoning = QLabel("")
        self._outlook_reasoning.setStyleSheet(f"color:{DIM}; {FONT_SMALL}")
        self._outlook_reasoning.setWordWrap(True)
        layout.addWidget(self._outlook_reasoning)

        self._reasoning = QLabel("")
        self._reasoning.setStyleSheet(f"color:{DIM}; {FONT_SMALL}")
        self._reasoning.setWordWrap(True)
        layout.addWidget(self._reasoning)

        self._high_conf = QLabel("")
        self._high_conf.setStyleSheet(f"color:{HIGH_CONFIDENCE_COLOR}; {FONT_TINY} font-weight:700;")
        self._high_conf.setVisible(False)
        layout.addWidget(self._high_conf)

        layout.addStretch(1)

        self._footer = QLabel("")
        self._footer.setStyleSheet(f"color:{DIM}; {FONT_SMALL}")
        layout.addWidget(self._footer)

        self._trade_status = QLabel("Manual tracking only — no orders are placed")
        self._trade_status.setStyleSheet(f"color:{DIM}; {FONT_SMALL}")
        self._trade_status.setWordWrap(True)
        layout.addWidget(self._trade_status)

        trade_buttons = QHBoxLayout()
        self._entered_button = QPushButton("I entered")
        self._entered_button.setToolTip(
            "Record a position you already entered manually. The app does not synchronize TradingView positions or place orders."
        )
        self._entered_button.clicked.connect(self.record_manual_entry)
        self._entered_button.setEnabled(False)
        self._exit_button = QPushButton("Exit confirmed")
        self._exit_button.setToolTip("Record an exit you already executed yourself; this does not place an order.")
        self._exit_button.clicked.connect(self.record_manual_exit)
        self._exit_button.setEnabled(False)
        trade_buttons.addWidget(self._entered_button)
        trade_buttons.addWidget(self._exit_button)
        layout.addLayout(trade_buttons)

        self._status = QLabel("")
        self._status.setStyleSheet(f"color:{DIM}; {FONT_SMALL}")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

    def _level_label(self) -> QLabel:
        label = QLabel("—")
        label.setStyleSheet(f"color:{DIM}; {FONT_SMALL}")
        return label

    def _position(self) -> None:
        screen = QGuiApplication.primaryScreen()
        self.setFixedWidth(PANEL_WIDTH)
        self.adjustSize()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.right() - PANEL_WIDTH - SCREEN_MARGIN, geo.top() + SCREEN_MARGIN)

    # -- Public rendering API -----------------------------------------------

    def show_signal(self, view: SignalView) -> None:
        """Render a signal view and start (or refresh) its age clock."""
        self._view = view
        self._action.setText(view.action_label)
        self._action.setStyleSheet(f"color:{view.color}; font-size:20px; font-weight:800;")
        currency_label = f" · {view.currency}" if view.currency else ""
        self._symbol.setText(f"{view.symbol} · {view.market}{currency_label}")
        self._confidence.setText(view.confidence)
        self._entry.setText(f"ENTRY {view.entry}")
        self._sl.setText(f"SL {view.stop_loss}")
        self._target.setText(f"TARGET {view.target}")
        self._size.setText(f"SIZE {view.size}")
        self._qty.setText(f"QTY {view.qty}")
        self._timeframe.setText(f"TF {view.timeframe}")
        self._outlook.setText(view.outlook_label)
        self._outlook_reasoning.setText(view.outlook_reasoning)
        self._reasoning.setText(view.reasoning)
        self.set_status("")
        self.show()
        self.adjustSize()
        self._set_highlight(view.is_high_confidence)
        self._update_age()

    def _set_highlight(self, high: bool) -> None:
        """Toggle the high-confidence visual: amber border + badge."""
        border = HIGH_BORDER if high else BORDER
        self._card.setStyleSheet(
            f"#card {{ background-color: {PANEL_BG}; border: 2px solid {border}; border-radius: 10px; }}"
        )
        self._high_conf.setVisible(high)
        self._high_conf.setText("⚡ HIGH CONFIDENCE" if high else "")

    def show_error(self, message: str) -> None:
        self._status.setStyleSheet(f"color:{ERROR}; {FONT_SMALL}")
        self._status.setText(f"⚠ {message}")

    def set_status(self, message: str) -> None:
        self._status.setStyleSheet(f"color:{DIM}; {FONT_SMALL}")
        self._status.setText(message)

    def snapshot(self) -> dict[str, str]:
        """Current label texts, for tests and debugging."""
        return {
            "action": self._action.text(),
            "symbol_market": self._symbol.text(),
            "confidence": self._confidence.text(),
            "entry": self._entry.text(),
            "stop_loss": self._sl.text(),
            "target": self._target.text(),
            "size": self._size.text(),
            "qty": self._qty.text(),
            "timeframe": self._timeframe.text(),
            "outlook": self._outlook.text(),
            "outlook_reasoning": self._outlook_reasoning.text(),
            "reasoning": self._reasoning.text(),
            "footer": self._footer.text(),
            "status": self._status.text(),
            "high_conf": self._high_conf.text(),
            "trade_status": self._trade_status.text(),
            "entered_button": self._entered_button.text(),
            "exit_button": self._exit_button.text(),
        }

    # -- Slots --------------------------------------------------------------

    def _on_signal(self, result: "ReadResult") -> None:
        if result.frame_unchanged:
            self.set_status("Chart unchanged — skipped provider request")
            return
        if result.flip_suppressed:
            self.set_status("Flip suppressed — keeping the previous call")
            return
        if result.error:
            self.show_error(result.error)
            return
        if result.ctx is not None:
            self._ctx = result.ctx
            if self._cfg is not None:
                signal = result.ctx.signal
                # Compute risk-based quantity and attach to signal.
                market_account_size = account_size_for_market(self._cfg, signal.market)
                qty = compute_position_size(
                    market_account_size,
                    self._cfg.risk_per_trade_pct,
                    signal.entry,
                    signal.stop_loss,
                )
                signal.quantity = qty
                view = SignalView.from_context(
                    result.ctx,
                    high_confidence_threshold=self._cfg.high_confidence_threshold,
                )
                alert(signal, self._cfg.high_confidence_threshold)
            else:
                view = result.to_view()
            self.show_signal(view)
            if result.ctx.model == "noop/mock":
                self.show_error("TEST MODE — no model analysis. Do not trade from this output.")
            self._refresh_trade_controls()

    def _refresh_trade_controls(self) -> None:
        """Show the persisted manual position and gate tracking controls."""
        active = self._manual_tracker.active_trade
        if active is not None:
            self._trade_status.setText(
                f"ACTIVE MANUAL POSITION: {active['side'].upper()} {active['quantity']} "
                f"{active['symbol']} @ {float(active['actual_entry']):g} {active.get('currency') or ''} · "
                "recorded only; no order was placed"
            )
            self._entered_button.setText("Position tracked")
            self._entered_button.setToolTip("A local position is already tracked. Exit recording does not place orders.")
            self._entered_button.setEnabled(False)
            self._exit_button.setEnabled(True)
            return

        self._trade_status.setText(
            "No local position tracked · TradingView positions are not synchronized; no orders are placed"
        )
        signal = self._ctx.signal if self._ctx is not None else None
        identified_real_signal = (
            signal is not None
            and self._ctx is not None
            and self._ctx.model != "noop/mock"
            and bool(str(signal.symbol or "").strip())
            and str(signal.market or "").upper() in {"NSE", "BSE", "US"}
            and signal.action in {"buy", "sell", "hold", "watch"}
        )
        actionable_recommendation = (
            identified_real_signal
            and signal.action in {"buy", "sell"}
            and all(value is not None for value in (signal.entry, signal.stop_loss, signal.target))
        )
        if actionable_recommendation:
            self._entered_button.setText("I entered")
            self._entered_button.setToolTip(
                "Record the actual fill for the displayed BUY/SELL recommendation; no order will be placed."
            )
            self._entered_button.setEnabled(True)
        elif identified_real_signal:
            self._entered_button.setText("Track open")
            self._entered_button.setToolTip(
                "Record an already-open position after confirming its side, actual fill and quantity. "
                "TradingView positions are not synchronized and no orders will be placed."
            )
            self._entered_button.setEnabled(True)
        else:
            self._entered_button.setText("I entered")
            self._entered_button.setToolTip(
                "A real, identified equity signal is required before recording a position."
            )
            self._entered_button.setEnabled(False)
        self._exit_button.setEnabled(False)

    def record_manual_entry(self) -> None:
        """Record a user-confirmed fill or an already-open external position; never places an order."""
        if self._manual_tracker.active_trade is not None:
            self.show_error("A tracked position is already active. Record its exit first.")
            self._refresh_trade_controls()
            return
        if self._ctx is None:
            self.show_error("Read a real chart signal before recording a position.")
            return
        signal = self._ctx.signal
        if self._ctx.model == "noop/mock":
            self.show_error("TEST MODE output cannot be used to record a trade.")
            return
        if not str(signal.symbol or "").strip() or str(signal.market or "").upper() not in {"NSE", "BSE", "US"}:
            self.show_error("A clearly identified NSE, BSE or US equity is required to record a position.")
            return

        has_actionable_setup = (
            signal.action in {"buy", "sell"}
            and all(value is not None for value in (signal.entry, signal.stop_loss, signal.target))
        )
        tracking_ctx = self._ctx
        recording_existing_position = not has_actionable_setup

        if recording_existing_position:
            answer = QMessageBox.question(
                self,
                "Track existing position",
                "This app does not synchronize TradingView positions. Continue only if you already "
                "opened this position yourself. This records it locally; it does not place or verify orders.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            side_choice, accepted = QInputDialog.getItem(
                self,
                "Track existing position",
                "Select the side of the position already open:",
                ["Long (BUY)", "Short (SELL)"],
                0,
                False,
            )
            if not accepted:
                return
            tracking_side = "sell" if side_choice.startswith("Short") else "buy"
            # Do not save unverified model-generated levels as levels for the
            # user's already-open external position.
            tracking_signal = signal.model_copy(
                update={
                    "action": tracking_side,
                    "entry": None,
                    "stop_loss": None,
                    "target": None,
                    "position_size_pct": None,
                    "quantity": None,
                    "reasoning": "Existing position recorded from user-confirmed external account; trade levels are not synchronized.",
                }
            )
            tracking_ctx = replace(self._ctx, signal=tracking_signal)
        else:
            tracking_side = signal.action

        if tracking_side == "sell":
            answer = QMessageBox.question(
                self,
                "Confirm short-position tracking",
                "For NSE cash equities, a SELL signal may mean exiting shares you already own. "
                "Recording SELL here is treated as a short position. Continue only if your chosen "
                "product/account supports that position. No order will be placed.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        default_entry = float(signal.entry or 0.0) if not recording_existing_position else 0.0
        actual_entry, accepted = QInputDialog.getDouble(
            self,
            "Confirm manual entry",
            f"Enter your ACTUAL fill price for {tracking_side.upper()} {signal.symbol}. "
            "This only records a position you already executed yourself:",
            default_entry,
            0.0,
            1_000_000_000_000.0,
            4,
        )
        if not accepted:
            return
        guessed_quantity = getattr(signal, "quantity", None) if not recording_existing_position else None
        quantity_default = max(0.0001, float(guessed_quantity)) if guessed_quantity else 1.0
        if signal.market == "US":
            quantity, accepted = QInputDialog.getDouble(
                self,
                "Confirm executed quantity",
                "Enter the actual shares you filled (fractional US shares are allowed where supported):",
                quantity_default,
                0.0001,
                1_000_000_000.0,
                4,
            )
        else:
            quantity, accepted = QInputDialog.getInt(
                self,
                "Confirm executed quantity",
                "Enter the actual whole number of Indian cash-equity shares you filled:",
                max(1, round(quantity_default)),
                1,
                1_000_000_000,
                1,
            )
        if not accepted:
            return
        try:
            self._manual_tracker.open_trade(tracking_ctx, actual_entry, quantity)
        except ManualTradeError as exc:
            self.show_error(str(exc))
            self._refresh_trade_controls()
            return
        self._refresh_trade_controls()
        if recording_existing_position:
            self.set_status(
                "Existing position recorded locally. TradingView is not synced; no order was placed."
            )
        else:
            self.set_status(
                "Entry recorded locally. No order was placed. Use Exit confirmed only after you close it."
            )

    def record_manual_exit(self) -> None:
        """Ask for the actual user exit fill and append the closed trade to CSV."""
        active = self._manual_tracker.active_trade
        if active is None:
            self.show_error("There is no tracked position to close.")
            self._refresh_trade_controls()
            return
        actual_exit, accepted = QInputDialog.getDouble(
            self,
            "Confirm manual exit",
            f"Enter your ACTUAL exit fill price for {active['side'].upper()} "
            f"{active['symbol']} ({active['quantity']} shares):",
            0.0,
            0.0,
            1_000_000_000_000.0,
            4,
        )
        if not accepted:
            return
        try:
            closed = self._manual_tracker.close_trade(actual_exit)
        except ManualTradeError as exc:
            self.show_error(str(exc))
            self._refresh_trade_controls()
            return
        self._refresh_trade_controls()
        pnl = float(closed["gross_pnl_before_costs"])
        self.set_status(
            f"Exit recorded · gross P&L {closed.get('currency') or ''} {pnl:+,.2f} before fees/slippage · "
            f"held {format_age(float(closed['holding_seconds']))}. "
            "Saved to Manual_Trade_Journal.csv; no order was placed."
        )

    def _update_age(self) -> None:
        if self._view is None:
            return
        view = self._view
        color = STALE if view.is_stale() else DIM
        self._footer.setStyleSheet(f"color:{color}; {FONT_SMALL}")
        self._footer.setText(f"{view.provider_model} · signal {view.age_text()} old")

    # -- Dragging & context menu (frameless window affordances) -------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().x() - self.x(),
                event.globalPosition().y() - self.y(),
            )

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(
                int(event.globalPosition().x() - self._drag_offset[0]),
                int(event.globalPosition().y() - self._drag_offset[1]),
            )

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._drag_offset = None

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        menu = QMenu(self)
        read_action = menu.addAction("Read now")
        log_action = menu.addAction("Log trade")
        watch_action = menu.addAction(
            "Pause monitoring" if self._watch_enabled else "Resume monitoring"
        )
        quit_action = menu.addAction("Quit")
        chosen = menu.exec(event.globalPos())
        if chosen is read_action:
            self.read_requested.emit()
        elif chosen is log_action:
            self.log_requested.emit()
        elif chosen is watch_action:
            self.watch_toggle_requested.emit()
        elif chosen is quit_action:
            self.close()

    def set_watch_enabled(self, enabled: bool) -> None:
        """Update monitoring state and the visible status line."""
        self._watch_enabled = enabled
        if enabled:
            self.set_status("Monitoring active — right-click to pause")
        else:
            self.set_status("Monitoring paused — right-click to resume")

    def log_current_signal(self) -> None:
        if self._trade_logger is None:
            self.show_error("Trade logger is not configured")
            return
        if self._ctx is None:
            self.show_error("No active signal to log")
            return
        try:
            self._trade_logger.log_signal(self._ctx)
            self.set_status("Trade logged successfully")
        except Exception as exc:
            self.show_error(f"Failed to log: {exc}")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._clock.stop()
        self.closed.emit()
        super().closeEvent(event)


def configure_high_dpi() -> None:
    """Configure crisp fractional High-DPI scaling on Windows 4K/high-res displays."""
    if QApplication.instance() is None:
        if hasattr(Qt.HighDpiScaleFactorRoundingPolicy, "PassThrough"):
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )


def run_overlay(cfg: Config, hotkey: str = "f8", watch: bool = False) -> int:
    """Run the always-on-top overlay: reads on F8 (or right-click) render into
    the floating panel. When ``watch`` is true, poll at the configured interval
    and skip unchanged frames before provider inference. Returns the process
    exit code."""
    from ai_trader.capture import FrameGate
    from ai_trader.cli import build_pipeline, read_signal  # lazy: avoid import cycle

    configure_high_dpi()
    app = QApplication.instance() or QApplication([])
    client, engine = build_pipeline(cfg)
    trade_logger = TradeLogger(excel_path=cfg.excel_path)

    manual_tracker = ManualTradeTracker()
    window = OverlayWindow(
        trade_logger=trade_logger,
        cfg=cfg,
        manual_tracker=manual_tracker,
        watch_enabled=watch,
    )
    poll_interval_seconds = minimum_safe_poll_interval_seconds(
        cfg.interval_seconds,
        cfg.calls_per_minute,
        cfg.min_interval_seconds,
    )
    poll_interval_ms = max(1_000, round(poll_interval_seconds * 1_000))
    if watch:
        window.set_status(
            f"Monitoring every {poll_interval_seconds:g}s — right-click to pause; "
            f"{hotkey.upper()} to read now"
        )
    else:
        window.set_status(f"Ready — press {hotkey.upper()} to read, {cfg.log_hotkey.upper()} to log")
    window.show()

    busy_lock = threading.Lock()
    busy = {"value": False}
    frame_gate = FrameGate()

    def _read_worker(skip_unchanged: bool) -> None:
        try:
            window.signal_ready.emit(
                read_signal(
                    cfg,
                    client,
                    engine,
                    frame_gate=frame_gate,
                    skip_unchanged=skip_unchanged,
                )
            )
        except RuntimeError:  # window closed mid-read; panel is gone
            pass
        except Exception as exc:  # pragma: no cover - read_signal already catches
            window.error_reported.emit(f"{type(exc).__name__}: {exc}")
        finally:
            with busy_lock:
                busy["value"] = False

    def perform_read(skip_unchanged: bool = False) -> None:
        with busy_lock:
            if busy["value"]:
                return
            busy["value"] = True
        window.status_changed.emit("Reading…")
        threading.Thread(target=_read_worker, args=(skip_unchanged,), daemon=True).start()

    window.read_requested.connect(perform_read)
    window.closed.connect(app.quit)

    poll_timer = QTimer(window)
    poll_timer.timeout.connect(lambda: perform_read(skip_unchanged=True))
    if watch:
        poll_timer.start(poll_interval_ms)
        QTimer.singleShot(0, lambda: perform_read(skip_unchanged=True))

    def toggle_monitoring() -> None:
        if poll_timer.isActive():
            poll_timer.stop()
            window.set_watch_enabled(False)
        else:
            poll_timer.start(poll_interval_ms)
            window.set_watch_enabled(True)
            perform_read(skip_unchanged=True)

    window.watch_toggle_requested.connect(toggle_monitoring)
    window.closed.connect(poll_timer.stop)

    keyboard = None
    try:
        import keyboard
    except ImportError:
        keyboard = None
    if keyboard is not None:
        try:
            keyboard.add_hotkey(hotkey, perform_read)
            keyboard.add_hotkey(cfg.log_hotkey, window.log_requested.emit)
        except Exception as exc:
            logger.warning("global hotkey unavailable (%s); using the panel menu", exc)
            window.set_status("Ready — right-click to read/log (hotkeys unavailable)")

    try:
        app.exec()
    finally:
        if keyboard is not None:
            try:
                keyboard.remove_all_hotkeys()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
        client.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - manual run path
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(run_overlay(load_config()))
