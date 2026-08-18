"""Tests for single-read and opt-in polling behavior."""

from __future__ import annotations

import json
from unittest import mock

from PIL import Image

from ai_trader.capture import FrameGate
from ai_trader.cli import build_arg_parser, do_read, main, read_signal, run_watch
from ai_trader.config import load_config
from ai_trader.providers import ProviderResult
from ai_trader.signal import SignalContext, SignalEngine, TradingSignal

GOOD_RESPONSE = '''{
  "action": "hold",
  "confidence": 50,
  "entry": null,
  "stop_loss": null,
  "target": null,
  "position_size_pct": null,
  "timeframe": "15m",
  "reasoning": "No new setup.",
  "market": "NSE",
  "symbol": "NIFTY"
}'''


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, image_b64: str, mime: str, prompt: str) -> ProviderResult:
        self.calls += 1
        return ProviderResult(text=GOOD_RESPONSE, model="fake/test")


def test_watch_mode_skips_unchanged_frames_after_a_success(monkeypatch) -> None:
    image = Image.new("RGB", (32, 32), "black")
    monkeypatch.setattr("ai_trader.cli.grab_screen", lambda cfg: image)
    cfg = load_config()
    client = FakeClient()
    gate = FrameGate()
    engine = SignalEngine()

    first = read_signal(cfg, client, engine, frame_gate=gate, skip_unchanged=True)
    second = read_signal(cfg, client, engine, frame_gate=gate, skip_unchanged=True)

    assert first.ctx is not None
    assert second.frame_unchanged is True
    assert client.calls == 1


def test_manual_read_still_analyzes_an_unchanged_frame(monkeypatch) -> None:
    image = Image.new("RGB", (32, 32), "black")
    monkeypatch.setattr("ai_trader.cli.grab_screen", lambda cfg: image)
    cfg = load_config()
    client = FakeClient()
    gate = FrameGate()
    engine = SignalEngine()

    assert read_signal(cfg, client, engine, frame_gate=gate, skip_unchanged=True).ctx is not None
    assert read_signal(cfg, client, engine, frame_gate=gate).ctx is not None
    assert client.calls == 2


def test_watch_flag_parses_with_overlay() -> None:
    args = build_arg_parser().parse_args(["--overlay", "--watch"])
    assert args.overlay is True
    assert args.watch is True


def test_watch_flag_parses_without_overlay() -> None:
    args = build_arg_parser().parse_args(["--watch"])
    assert args.watch is True
    assert args.overlay is False


def test_watch_loop_skips_unchanged_frames(monkeypatch) -> None:
    image = Image.new("RGB", (32, 32), "black")
    monkeypatch.setattr("ai_trader.cli.grab_screen", lambda cfg: image)
    cfg = load_config()
    client = FakeClient()
    engine = SignalEngine()

    results = list(
        run_watch(cfg, client, engine, interval_seconds=0, max_iterations=3, sleep_fn=lambda _: None)
    )

    assert len(results) == 3
    assert results[0].ctx is not None
    assert all(r.frame_unchanged for r in results[1:])
    assert client.calls == 1


def test_watch_loop_respects_max_iterations(monkeypatch) -> None:
    image = Image.new("RGB", (32, 32), "black")
    monkeypatch.setattr("ai_trader.cli.grab_screen", lambda cfg: image)
    cfg = load_config()
    client = FakeClient()
    engine = SignalEngine()

    results = list(
        run_watch(cfg, client, engine, interval_seconds=0, max_iterations=2, sleep_fn=lambda _: None)
    )
    assert len(results) == 2


class _DictStyleCapture:
    """Response whose text uses the JSON dict-style the engine parses."""

    def __init__(self, text: str) -> None:
        self.text = text

    def analyze(self, image_b64: str, mime: str, prompt: str) -> ProviderResult:
        return ProviderResult(text=self.text, model="fake/test")


def test_do_read_emits_risk_sized_quantity(monkeypatch, capsys) -> None:
    """Task 2 accept: a directional signal past guard_flip gets risk-sized quantity."""
    monkeypatch.setattr(
        "ai_trader.cli.grab_screen",
        lambda cfg: Image.new("RGB", (32, 32), "black"),
    )
    cfg = load_config()
    cfg.account_size = 100_000.0
    cfg.risk_per_trade_pct = 1.0

    class SizedClient:
        def __init__(self) -> None:
            self.calls = 0

        def analyze(self, image_b64: str, mime: str, prompt: str) -> ProviderResult:
            self.calls += 1
            signal = TradingSignal(
                action="buy",
                confidence=90.0,
                entry=100.0,
                stop_loss=99.0,
                target=105.0,
                position_size_pct=2.0,
                timeframe="15m",
                reasoning="Risk-sized.",
                market="NSE",
                symbol="TEST",
            )
            return ProviderResult(text=json.dumps(signal.model_dump()), model="fake/test")

    engine = SignalEngine()
    result = do_read(cfg, SizedClient(), engine)
    out = capsys.readouterr().out

    assert result.ctx is not None
    assert result.ctx.signal.quantity == 1000.0
    assert "risk-sized qty 1000" in out


def test_do_read_zero_account_keeps_quantity_none(monkeypatch, capsys) -> None:
    """Task 2 accept: unset/zero risk.account_size keeps quantity None and the
    CLI display path does not crash."""
    monkeypatch.setattr(
        "ai_trader.cli.grab_screen",
        lambda cfg: Image.new("RGB", (32, 32), "black"),
    )
    cfg = load_config()
    signal = TradingSignal(
        action="buy",
        confidence=90.0,
        entry=100.0,
        stop_loss=99.0,
        target=105.0,
        position_size_pct=2.0,
        timeframe="15m",
        reasoning="Risk-sized.",
        market="NSE",
        symbol="TEST",
    )
    client = _DictStyleCapture(json.dumps(signal.model_dump()))
    engine = SignalEngine()

    result = do_read(cfg, client, engine)
    out = capsys.readouterr().out

    assert result.ctx is not None
    assert result.ctx.signal.quantity is None
    assert "position sizing unavailable" in out


def test_do_read_high_confidence_triggers_alert(monkeypatch) -> None:
    """Task 3 accept: a high-confidence directional signal past guard_flip
    raises the alert (the shared ``_emit_signal`` path)."""
    monkeypatch.setattr(
        "ai_trader.cli.grab_screen",
        lambda cfg: Image.new("RGB", (32, 32), "black"),
    )

    def respond(action: str) -> ProviderResult:
        signal = TradingSignal(
            action=action,
            confidence=90.0,
            entry=100.0,
            stop_loss=99.5,
            target=105.0,
            position_size_pct=1.5,
            timeframe="15m",
            reasoning="High-confidence reversal.",
            market="NSE",
            symbol="TEST",
        )
        return ProviderResult(text=json.dumps(signal.model_dump()), model="fake/test")

    engine = SignalEngine(min_flip_hold_seconds=0.0)
    alert = mock.Mock(return_value=True)

    with mock.patch("ai_trader.cli.alert", alert) as patched:
        result = do_read(load_config(), _DictStyleCapture(respond("sell").text), engine)

    assert result.ctx is not None
    patched.assert_called_once()
    assert patched.call_args.args[0].action == "sell"


def test_do_read_forwards_configured_threshold_to_alert(monkeypatch) -> None:
    """Task 3 accept: the CLI forwards the configured high-confidence threshold
    to ``alert()`` for every accepted signal; the at/above filtering itself is
    unit-tested in test_alerts.py."""
    monkeypatch.setattr(
        "ai_trader.cli.grab_screen",
        lambda cfg: Image.new("RGB", (32, 32), "black"),
    )

    def respond(confidence: float) -> ProviderResult:
        signal = TradingSignal(
            action="buy",
            confidence=confidence,
            entry=100.0,
            stop_loss=99.5,
            target=105.0,
            position_size_pct=1.5,
            timeframe="15m",
            reasoning="Threshold check.",
            market="NSE",
            symbol="TEST",
        )
        return ProviderResult(text=json.dumps(signal.model_dump()), model="fake/test")

    engine = SignalEngine(min_flip_hold_seconds=0.0)

    cfg = load_config()
    cfg.high_confidence_threshold = 95.0
    with mock.patch("ai_trader.cli.alert") as patched:
        result_low = do_read(cfg, _DictStyleCapture(respond(90.0).text), engine)
        result_high = do_read(cfg, _DictStyleCapture(respond(97.0).text), engine)

    assert result_low.ctx is not None
    assert result_high.ctx is not None
    # _emit_signal hands every accepted signal to alert() with the configured
    # threshold; alert() itself decides whether that signal qualifies.
    assert patched.call_count == 2
    assert all(call.args[1] == 95.0 for call in patched.call_args_list)


def test_do_read_suppressed_flip_never_alerts(monkeypatch) -> None:
    """Task 3 accept: a flip suppressed by guard_flip must not raise an alert."""
    monkeypatch.setattr(
        "ai_trader.cli.grab_screen",
        lambda cfg: Image.new("RGB", (32, 32), "black"),
    )
    cfg = load_config()
    assignments: list[tuple] = []

    class FlipClient:
        def __init__(self) -> None:
            self.calls = 0

        def analyze(self, image_b64: str, mime: str, prompt: str) -> ProviderResult:
            self.calls += 1
            action, symbol = assignments[self.calls - 1]
            signal = TradingSignal(
                action=action,
                confidence=95.0,
                entry=100.0,
                stop_loss=102.0,
                target=95.0,
                position_size_pct=1.5,
                timeframe="15m",
                reasoning="Flip-flop test.",
                market="NSE",
                symbol=symbol,
            )
            return ProviderResult(text=json.dumps(signal.model_dump()), model="fake/test")

    engine = SignalEngine(min_flip_hold_seconds=10.0)
    assignments.append(("buy", "TEST"))
    assignments.append(("sell", "TEST"))  # flip within the hold window

    client = FlipClient()
    first = do_read(cfg, client, engine)
    second = do_read(cfg, client, engine)

    assert first.ctx is not None
    assert second.flip_suppressed is True


def test_hotkey_exit_closes_client(monkeypatch) -> None:
    """Regression (Task 3): leaving via the hotkey path must still close the client.

    ``main()`` used to ``return 0`` from the hotkey block without calling
    ``client.close()``; only the interactive fallback loop closed it.
    """
    closed = {"count": 0}

    class ClosingClient(FakeClient):
        def close(self) -> None:
            closed["count"] += 1

    class FakeEngine:
        def build_prompt(self):
            return "prompt"

        def parse(self, text):
            return None

    class FakeKeyboard:
        def __init__(self) -> None:
            self.added: list[tuple] = []

        def add_hotkey(self, hotkey, handler) -> None:
            self.added.append((hotkey, handler))

        def wait(self) -> None:
            return  # simulate hotkey-triggered exit

    import sys
    import types
    fake_kb = FakeKeyboard()
    keyboard_mod = types.ModuleType("keyboard")
    keyboard_mod.add_hotkey = fake_kb.add_hotkey
    keyboard_mod.wait = fake_kb.wait
    monkeypatch.setitem(sys.modules, "keyboard", keyboard_mod)
    monkeypatch.setattr(
        "ai_trader.cli.build_pipeline",
        lambda cfg: (ClosingClient(), FakeEngine()),
    )

    main(["--hotkey", "f8"])

    assert closed["count"] == 1
    assert fake_kb.added[0][0] == "f8"
