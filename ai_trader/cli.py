"""AI Trader CLI — Phase 1 signal reader plus Phase 2 overlay launcher.

A global hotkey (default F8) captures the configured screen region, sends it
through the vision-LLM fallback chain, and prints the structured signal to the
console. Falls back to an interactive "press Enter" loop if the global hook is
unavailable (e.g. admin rights) or the ``keyboard`` package cannot hook.

``--overlay`` launches the Phase 2 always-on-top PyQt6 panel (plan §2.5)
instead: reads run on a worker thread and render into the floating panel.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from ai_trader.capture import FrameGate, frame_digest, grab_screen, image_to_base64_png
from ai_trader.config import load_config, with_region
from ai_trader.overlay import SignalView
from ai_trader.providers import ProviderError, VisionClient, make_provider_chain
from ai_trader.rate_guard import RateGuard
from ai_trader.signal import SignalContext, SignalEngine
from ai_trader.logger import TradeLogger


def build_pipeline(cfg):
    guard = RateGuard(calls_per_minute=cfg.calls_per_minute, min_interval_seconds=cfg.min_interval_seconds)
    client = VisionClient(
        providers=make_provider_chain(cfg),
        guard=guard,
        cooldown_seconds=cfg.cooldown_seconds,
    )
    engine = SignalEngine(
        market=cfg.market,
        symbol_hint=cfg.symbol_hint,
        min_flip_hold_seconds=cfg.min_flip_hold_seconds,
    )
    return client, engine


@dataclass(frozen=True)
class ReadResult:
    """One screen read: the parsed signal plus the metadata needed to render it.

    Never carries an exception — errors and flip-suppression are first-class
    outcomes so both the CLI printer and the overlay window can react to them.
    """

    ctx: SignalContext | None = None
    error: str | None = None
    flip_suppressed: bool = False
    frame_unchanged: bool = False
    elapsed: float = 0.0

    def to_view(self) -> SignalView | None:
        """A renderable :class:`SignalView`, or ``None`` when there is nothing."""
        return SignalView.from_context(self.ctx) if self.ctx is not None else None


def read_signal(
    cfg,
    client,
    engine,
    frame_gate: FrameGate | None = None,
    skip_unchanged: bool = False,
) -> ReadResult:
    """Capture the configured region once and return a structured result.

    Never raises: every failure path (provider error, parse error, capture
    failure) becomes a ``ReadResult`` with ``error`` set, so the CLI printer
    and the overlay window share one code path. In watch mode, an unchanged
    frame returns ``frame_unchanged`` before any provider request is made.
    """
    started = time.monotonic()
    try:
        image = grab_screen(cfg)
        captured_at = time.time()
        digest = frame_digest(image)
        if frame_gate is not None and skip_unchanged and not frame_gate.is_new(digest):
            return ReadResult(frame_unchanged=True, elapsed=time.monotonic() - started)
        image_b64 = image_to_base64_png(image)
        prompt = engine.build_prompt()
        result = client.analyze(image_b64, "image/png", prompt)
        signal = engine.parse(result.text)
        elapsed = time.monotonic() - started
        if frame_gate is not None:
            frame_gate.mark_processed(digest)
        # Suppress a buy/sell flip that happened too fast (plan §6: no flip on
        # ordinary chart noise). Hold/watch passes are never suppressed.
        if not engine.guard_flip(signal.symbol, signal):
            return ReadResult(flip_suppressed=True, elapsed=elapsed)
        ctx = SignalContext(
            symbol=signal.symbol,
            signal=signal,
            captured_at=captured_at,
            provider=result.model.partition("/")[0],
            model=result.model,
        )
        return ReadResult(ctx=ctx, elapsed=elapsed)
    except (ProviderError, ValueError) as exc:
        return ReadResult(error=str(exc), elapsed=time.monotonic() - started)
    except Exception as exc:  # capture or unexpected failures
        return ReadResult(error=f"{type(exc).__name__}: {exc}", elapsed=time.monotonic() - started)


def do_read(cfg, client, engine) -> ReadResult:
    """Capture once and print the resulting signal (or the error)."""
    result = read_signal(cfg, client, engine)
    if result.frame_unchanged:
        print("\n--- FRAME UNCHANGED ---")
        print("Chart has not changed since the last successful read; skipped provider request.")
        return result
    if result.flip_suppressed:
        print("\n--- FLIP SUPPRESSED ---")
        print("Signal action flipped within the hold window — keeping the previous call.")
        return result
    if result.error:
        print(f"\n[error] {result.error}", file=sys.stderr)
        return result
    assert result.ctx is not None
    signal = result.ctx.signal
    print("\n--- SIGNAL ---")
    print(json.dumps(signal.model_dump(), indent=2))
    print(f"provider/model: {result.ctx.model}   ({result.elapsed:.1f}s)")
    return result


def run_watch(
    cfg,
    client,
    engine,
    interval_seconds: float | None = None,
    max_iterations: int | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> Iterator[ReadResult]:
    """Poll the configured region at ``interval_seconds``, skipping unchanged
    frames before any provider request (plan §3: continuous polling). Yields
    one :class:`ReadResult` per cycle.

    ``max_iterations`` and ``sleep_fn`` exist so offline tests can drive the
    loop without a wall clock.
    """
    frame_gate = FrameGate()
    interval = interval_seconds if interval_seconds is not None else cfg.interval_seconds
    sleep = sleep_fn if sleep_fn is not None else time.sleep
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        started = time.monotonic()
        result = read_signal(cfg, client, engine, frame_gate=frame_gate, skip_unchanged=True)
        iterations += 1
        yield result
        elapsed = time.monotonic() - started
        if max_iterations is None or iterations < max_iterations:
            sleep(max(0.0, interval - elapsed))


def run_cli_watch(
    cfg,
    client,
    engine,
    interval_seconds: float | None = None,
    max_iterations: int | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> int:
    """Continuous polling for the CLI (no PyQt6 overlay needed): prints each
    new signal, stays silent on unchanged frames, exits cleanly on Ctrl+C.
    Returns the process exit code."""
    try:
        for result in run_watch(
            cfg,
            client,
            engine,
            interval_seconds=interval_seconds,
            max_iterations=max_iterations,
            sleep_fn=sleep_fn,
        ):
            if result.frame_unchanged:
                continue
            if result.flip_suppressed:
                print("\n--- FLIP SUPPRESSED ---")
                print("Signal action flipped within the hold window — keeping the previous call.")
                continue
            if result.error:
                print(f"\n[error] {result.error}", file=sys.stderr)
                continue
            assert result.ctx is not None
            signal = result.ctx.signal
            print("\n--- SIGNAL ---")
            print(json.dumps(signal.model_dump(), indent=2))
            print(f"provider/model: {result.ctx.model}   ({result.elapsed:.1f}s)")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        client.close()
    return 0


def parse_region(value: str):
    left, top, width, height = (int(part) for part in value.split(","))
    return left, top, width, height


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-trader",
        description="AI Trader — signal reader (Phase 1) + floating overlay (Phase 2). Advisory only; never executes trades.",
    )
    parser.add_argument("--config", default=None, help="path to config.toml")
    parser.add_argument("--hotkey", default="f8", help="global hotkey to trigger a read (default: f8)")
    parser.add_argument("--once", action="store_true", help="take a single read and exit")
    parser.add_argument(
        "--region",
        default=None,
        help="override capture region as left,top,width,height (e.g. 100,50,900,600)",
    )
    parser.add_argument(
        "--overlay",
        action="store_true",
        help="run the always-on-top Phase 2 panel (requires PyQt6)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="with --overlay, poll at the configured interval and skip unchanged frames",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config(args.config)
    if args.region:
        cfg = with_region(cfg, *parse_region(args.region))

    if args.overlay:
        try:
            from ai_trader.overlay_ui import run_overlay  # lazy: PyQt6 optional
        except ImportError:
            print(
                "[error] PyQt6 is required for --overlay. Install it with: pip install PyQt6",
                file=sys.stderr,
            )
            return 1
        return run_overlay(cfg, args.hotkey, watch=args.watch)

    client, engine = build_pipeline(cfg)
    trade_logger = TradeLogger(excel_path=cfg.excel_path)

    if args.once:
        do_read(cfg, client, engine)
        client.close()
        return 0

    if args.watch:
        return run_cli_watch(cfg, client, engine)

    last_result: ReadResult | None = None

    def on_read():
        nonlocal last_result
        last_result = do_read(cfg, client, engine)

    def on_log():
        if last_result and last_result.ctx:
            try:
                trade_logger.log_signal(last_result.ctx)
                print(f"--- LOGGED TRADE TO {cfg.excel_path} ---")
            except Exception as exc:
                print(f"\n[error] Failed to log trade: {exc}", file=sys.stderr)
        else:
            print("\n[error] No active signal to log.")

    # Prefer a global hotkey; degrade gracefully to an interactive loop.
    try:
        import keyboard
    except ImportError:
        keyboard = None

    if keyboard is not None:
        try:
            keyboard.add_hotkey(args.hotkey, on_read)
            keyboard.add_hotkey(cfg.log_hotkey, on_log)
            print(f"AI Trader ready. Press {args.hotkey.upper()} to read, {cfg.log_hotkey.upper()} to log. Ctrl+C to exit.")
            try:
                keyboard.wait()
            finally:
                client.close()
            return 0
        except Exception as exc:
            print(f"global hotkey unavailable ({exc}); falling back to interactive loop")
            keyboard = None

    print(f"AI Trader ready. Press Enter to read, type 'log' to log. Ctrl+C to exit.")
    try:
        while True:
            cmd = input().strip().lower()
            if cmd == "log":
                on_log()
            else:
                on_read()
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
