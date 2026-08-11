"""Tests for single-read and opt-in polling behavior."""

from __future__ import annotations

from PIL import Image

from ai_trader.capture import FrameGate
from ai_trader.cli import build_arg_parser, read_signal, run_watch
from ai_trader.config import load_config
from ai_trader.providers import ProviderResult
from ai_trader.signal import SignalEngine

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
    monkeypatch.setattr("ai_trader.cli.grab_region", lambda region: image)
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
    monkeypatch.setattr("ai_trader.cli.grab_region", lambda region: image)
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
    monkeypatch.setattr("ai_trader.cli.grab_region", lambda region: image)
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
    monkeypatch.setattr("ai_trader.cli.grab_region", lambda region: image)
    cfg = load_config()
    client = FakeClient()
    engine = SignalEngine()

    results = list(
        run_watch(cfg, client, engine, interval_seconds=0, max_iterations=2, sleep_fn=lambda _: None)
    )
    assert len(results) == 2
