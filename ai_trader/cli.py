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
import math
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from ai_trader.capture import FrameGate, frame_digest, grab_screen, image_to_base64_png
from ai_trader.config import account_size_for_market, load_config, with_region
from ai_trader.overlay import SignalView
from ai_trader.providers import ProviderError, VisionClient, make_provider_chain
from ai_trader.rate_guard import RateGuard
from ai_trader.signal import SignalContext, SignalEngine
from ai_trader.logger import TradeLogger
from ai_trader.alerts import alert
from ai_trader.risk import compute_position_size
from ai_trader.validation import downgrade_unsafe_signal, validate_signal_safety


# This screener intentionally supports equities, not crypto spot/perpetual pairs.
_CRYPTO_EXCHANGES = frozenset({
    "BINANCE", "COINBASE", "KRAKEN", "BITSTAMP", "BYBIT", "OKX",
    "BITFINEX", "KUCOIN", "GEMINI", "CRYPTO",
})
_CRYPTO_BASE_TICKERS = frozenset({
    "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "AVAX", "DOT",
    "LINK", "LTC", "BCH", "XLM", "TRX", "TON", "SHIB", "PEPE", "SUI",
    "APT", "NEAR", "ICP", "ETC", "FIL", "ARB", "OP", "UNI", "AAVE",
    "MKR", "ATOM", "XMR", "ALGO", "VET", "INJ", "SEI", "FET",
})
_CRYPTO_QUOTE_TICKERS = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USD", "EUR", "GBP", "BTC", "ETH", "BNB")


def _looks_like_crypto_symbol(symbol: str) -> bool:
    """Recognize common crypto pair symbols so AUTO mode cannot trade them as equities."""
    raw = str(symbol or "").strip().upper()
    if not raw:
        return False
    if ":" in raw:
        exchange, raw = raw.split(":", 1)
        if exchange.strip() in _CRYPTO_EXCHANGES:
            return True
    ticker = "".join(char for char in raw if char.isalnum())
    if ticker in _CRYPTO_BASE_TICKERS:
        return True
    return any(
        ticker.endswith(quote) and ticker[:-len(quote)] in _CRYPTO_BASE_TICKERS
        for quote in _CRYPTO_QUOTE_TICKERS
    )


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
        market_notes=cfg.market_notes,
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

    def to_view(self, high_confidence_threshold: float = 80.0) -> SignalView | None:
        """A renderable :class:`SignalView`, or ``None`` when there is nothing."""
        return (
            SignalView.from_context(self.ctx, high_confidence_threshold=high_confidence_threshold)
            if self.ctx is not None
            else None
        )


def _market_data_freshness(cfg, signal) -> tuple[float | None, bool | None]:
    """Return a vision-only freshness assessment; never call a market-data API.

    The local vision model must report explicit visible live status and a readable
    latest-candle timestamp. This is a visual claim, not independently verified
    market data; screenshots can be frozen or delayed even when captured now.
    """
    del cfg
    visible_time = str(getattr(signal, "latest_candle_time", "") or "").strip()
    verified_visually = (
        str(getattr(signal, "chart_status", "unknown") or "unknown").strip().lower() == "live"
        and bool(getattr(signal, "timestamp_visible", False))
        and bool(visible_time)
    )
    return None, verified_visually


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
        # The app accepts Indian cash equities and US-listed equities. In AUTO
        # mode, unsupported/ambiguous market output must never remain directional.
        supported_markets = {"NSE", "BSE", "US"}
        expected_market = str(cfg.market or "AUTO").strip().upper()
        actual_market = str(signal.market or "").strip().upper()
        if actual_market not in supported_markets:
            signal.action = "watch"
            signal.entry = signal.stop_loss = signal.target = None
            signal.currency = None
            signal.reasoning = (
                "NO TRADE: exchange/market could not be confidently identified from the visible chart. "
                + signal.reasoning
            ).strip()
        elif expected_market != "AUTO" and actual_market != expected_market:
            signal.action = "watch"
            signal.entry = signal.stop_loss = signal.target = None
            signal.currency = None
            signal.reasoning = (
                f"NO TRADE: configured market is {expected_market}, but the model read {actual_market}. "
                "Verify the chart symbol and exchange. " + signal.reasoning
            ).strip()
        elif _looks_like_crypto_symbol(signal.symbol):
            # The model can mislabel a crypto pair such as BTCUSD as "US".
            # Reject it before it reaches the overlay, sizing, or trade log.
            signal.action = "watch"
            signal.entry = signal.stop_loss = signal.target = None
            signal.position_size_pct = None
            signal.quantity = None
            signal.market = ""
            signal.currency = None
            signal.reasoning = (
                "NO TRADE: this screener supports NSE/BSE/US-listed equities, not cryptocurrency pairs. "
                "Verify the visible chart symbol and capture region. " + signal.reasoning
            ).strip()
        # Model output is not the safety authority. Apply deterministic checks before rendering.
        # No market-data API is used: directional calls require explicit visual freshness
        # evidence from the chart. This is not independent verification of live data.
        data_age_seconds, freshness_verified = _market_data_freshness(cfg, signal)

        # A WATCH is not an entry instruction. Do not let model-supplied levels
        # leak into the overlay, sizing, alerts, or trade journal.
        if signal.action == "watch":
            had_actionable_levels = any(
                value is not None
                for value in (
                    signal.entry,
                    signal.stop_loss,
                    signal.target,
                    signal.position_size_pct,
                    signal.quantity,
                )
            )
            signal.entry = signal.stop_loss = signal.target = None
            signal.position_size_pct = signal.quantity = None
            if had_actionable_levels:
                model_reason = signal.reasoning.strip()
                signal.reasoning = "WATCH is non-actionable; actionable levels were suppressed."
                if model_reason:
                    signal.reasoning += f" Model note: {model_reason}"
        elif signal.action == "hold" and freshness_verified is not True:
            # The vision-only pipeline cannot independently prove the candle age.
            # Keep the HOLD label but remove price levels that could be mistaken
            # for verified trade-management instructions.
            model_reason = signal.reasoning.strip()
            signal.entry = signal.stop_loss = signal.target = None
            signal.position_size_pct = signal.quantity = None
            signal.reasoning = (
                "Chart freshness is unverified: no entry, stop, target or size is endorsed. "
                "TradingView positions are not synchronized with this app."
            )
            if model_reason:
                signal.reasoning += f" Model note (unverified): {model_reason}"

        safety_errors = validate_signal_safety(
            action=signal.action,
            market=signal.market,
            symbol=signal.symbol,
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            target=signal.target,
            expected_market=expected_market,
            symbol_hint=str(getattr(cfg, "symbol_hint", "") or ""),
            data_age_seconds=data_age_seconds,
            freshness_verified=freshness_verified,
        )
        downgrade_unsafe_signal(signal, safety_errors)
        elapsed = time.monotonic() - started
        if frame_gate is not None:
            frame_gate.mark_processed(digest)
        # Suppress a buy/sell flip that happened too fast (plan §6: no flip on
        # ordinary chart noise). Hold/watch passes are never suppressed.
        market_symbol_key = f"{signal.market}:{signal.symbol}"
        if not engine.guard_flip(market_symbol_key, signal):
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


def _emit_signal(cfg, result: ReadResult) -> None:
    """Print an accepted signal with risk sizing, and alert if high-confidence.

    Only called for signals that already passed ``guard_flip`` (see the call
    sites in :func:`do_read` and :func:`run_cli_watch`), so a suppressed
    flip-flop is never announced.

    Advisory only: this prints and beeps; it never places a trade.
    """
    signal = result.ctx.signal
    if result.ctx.model == "noop/mock":
        print("\n[TEST MODE] No vision model produced this output. Do not treat it as a market signal.", file=sys.stderr)
    market_account_size = account_size_for_market(cfg, signal.market)
    qty = compute_position_size(
        market_account_size, cfg.risk_per_trade_pct, signal.entry, signal.stop_loss
    )
    signal.quantity = qty
    if qty is not None:
        guess = signal.position_size_pct if signal.position_size_pct is not None else "n/a"
        sizing = f"size | model guess {guess}% | risk-sized qty {qty:g}"
    else:
        if signal.market == "US":
            balance_hint = "risk.account_size_usd (USD)"
        elif signal.market in {"NSE", "BSE"}:
            balance_hint = "risk.account_size_inr (INR)"
        else:
            balance_hint = "a market-specific account balance"
        sizing = f"size | position sizing unavailable — configure {balance_hint} in config.toml"
    print("\n--- SIGNAL ---")
    print(json.dumps(signal.model_dump(), indent=2))
    print(f"provider/model: {result.ctx.model}   ({result.elapsed:.1f}s)")
    print(sizing)
    alert(signal, cfg.high_confidence_threshold)


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
    _emit_signal(cfg, result)
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
            _emit_signal(cfg, result)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        client.close()
    return 0


def parse_nonnegative_seconds(value: str) -> float:
    """Parse a finite, non-negative delay duration for CLI capture."""
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("delay must be a number of seconds") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError("delay must be a finite, non-negative number of seconds")
    return seconds


def parse_region(value: str) -> tuple[int, int, int, int]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError(f"region must be 'left,top,width,height', got {value!r}")
    try:
        left, top, width, height = (int(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"region coordinates must be integers, got {value!r}") from exc
    if width <= 0 or height <= 0:
        raise ValueError(f"region width and height must be positive, got width={width}, height={height}")
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
        "--delay",
        type=parse_nonnegative_seconds,
        default=0.0,
        metavar="SECONDS",
        help="with --once, wait before capturing so you can switch to the chart window",
    )
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
    # Launch the floating monitor by default when the app is started without
    # arguments. Explicit modes such as --once, --watch, or --hotkey preserve
    # their documented CLI behavior for scripting and troubleshooting.
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args = build_arg_parser().parse_args(raw_args)
    if not raw_args:
        args.overlay = True
        args.watch = True
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config(args.config)
    if args.region:
        try:
            cfg = with_region(cfg, *parse_region(args.region))
        except ValueError as exc:
            print(f"[error] Invalid region: {exc}", file=sys.stderr)
            return 2

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
        if args.delay > 0:
            print(
                f"\nCapturing in {args.delay:g} second(s) — switch to the trading chart now.",
                flush=True,
            )
            time.sleep(args.delay)
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
            except KeyboardInterrupt:
                print("\nStopped.")
            finally:
                try:
                    keyboard.remove_all_hotkeys()
                except Exception:
                    pass
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
