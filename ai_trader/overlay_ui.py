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
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ai_trader.config import Config, load_config
from ai_trader.overlay import SignalView
from ai_trader.logger import TradeLogger
from ai_trader.risk import compute_position_size

if TYPE_CHECKING:  # pragma: no cover - type-only; cli is lazily imported at runtime
    from ai_trader.cli import ReadResult
    from ai_trader.signal import SignalContext

logger = logging.getLogger(__name__)

# Panel palette.
PANEL_BG = "rgba(17, 20, 28, 235)"
BORDER = "rgba(255, 255, 255, 32)"
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
    ) -> None:
        super().__init__()
        self._view: SignalView | None = None
        self._ctx: "SignalContext" | None = None
        self._drag_offset: tuple[float, float] | None = None
        self._trade_logger = trade_logger
        self._cfg = cfg

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

        self._position()

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

        self._reasoning = QLabel("")
        self._reasoning.setStyleSheet(f"color:{DIM}; {FONT_SMALL}")
        self._reasoning.setWordWrap(True)
        layout.addWidget(self._reasoning)

        layout.addStretch(1)

        self._footer = QLabel("")
        self._footer.setStyleSheet(f"color:{DIM}; {FONT_SMALL}")
        layout.addWidget(self._footer)

        self._status = QLabel("")
        self._status.setStyleSheet(f"color:{DIM}; {FONT_SMALL}")
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
        self._symbol.setText(f"{view.symbol} · {view.market}")
        self._confidence.setText(view.confidence)
        self._entry.setText(f"ENTRY {view.entry}")
        self._sl.setText(f"SL {view.stop_loss}")
        self._target.setText(f"TARGET {view.target}")
        self._size.setText(f"SIZE {view.size}")
        self._qty.setText(f"QTY {view.qty}")
        self._timeframe.setText(f"TF {view.timeframe}")
        self._reasoning.setText(view.reasoning)
        self.set_status("")
        self.show()
        self.adjustSize()
        self._update_age()

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
            "reasoning": self._reasoning.text(),
            "footer": self._footer.text(),
            "status": self._status.text(),
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
                qty = compute_position_size(
                    self._cfg.account_size,
                    self._cfg.risk_per_trade_pct,
                    signal.entry,
                    signal.stop_loss,
                )
                if qty is not None:
                    signal.quantity = qty
                view = SignalView.from_context(result.ctx)
            else:
                view = result.to_view()
            self.show_signal(view)

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
        quit_action = menu.addAction("Quit")
        chosen = menu.exec(event.globalPos())
        if chosen is read_action:
            self.read_requested.emit()
        elif chosen is log_action:
            self.log_current_signal()
        elif chosen is quit_action:
            self.close()

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


def run_overlay(cfg: Config, hotkey: str = "f8", watch: bool = False) -> int:
    """Run the always-on-top overlay: reads on F8 (or right-click) render into
    the floating panel. When ``watch`` is true, poll at the configured interval
    and skip unchanged frames before provider inference. Returns the process
    exit code."""
    from ai_trader.capture import FrameGate
    from ai_trader.cli import build_pipeline, read_signal  # lazy: avoid import cycle

    app = QApplication.instance() or QApplication([])
    client, engine = build_pipeline(cfg)
    trade_logger = TradeLogger(excel_path=cfg.excel_path)

    window = OverlayWindow(trade_logger=trade_logger, cfg=cfg)
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

    poll_timer: QTimer | None = None
    if watch:
        poll_timer = QTimer(window)
        poll_timer.timeout.connect(lambda: perform_read(skip_unchanged=True))
        poll_timer.start(max(1_000, round(cfg.interval_seconds * 1_000)))
        window.closed.connect(poll_timer.stop)
        window.set_status(f"Watching every {cfg.interval_seconds:g}s — press {hotkey.upper()} to force a read")
        QTimer.singleShot(0, lambda: perform_read(skip_unchanged=True))

    keyboard = None
    try:
        import keyboard
    except ImportError:
        keyboard = None
    if keyboard is not None:
        try:
            keyboard.add_hotkey(hotkey, perform_read)
            keyboard.add_hotkey(cfg.log_hotkey, window.log_current_signal)
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
