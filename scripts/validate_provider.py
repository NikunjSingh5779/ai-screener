"""Phase 0 — provider validation.

Captures the configured screen region and prints each provider's RAW model output,
unparsed, so you can judge whether a free vision model can actually read your chart
(the plan's Phase 0 exit criterion).

Usage:
    python scripts\\validate_provider.py                # every provider in the chain
    python scripts\\validate_provider.py --provider openrouter
    python scripts\\validate_provider.py --region 100,50,900,600
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow running as a loose script (scripts/validate_provider.py) without install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_trader.capture import frame_digest, grab_region, image_to_base64_png  # noqa: E402
from ai_trader.config import load_config, with_region  # noqa: E402
from ai_trader.providers import make_provider_chain  # noqa: E402
from ai_trader.signal import SignalEngine  # noqa: E402


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_provider",
        description="Phase 0 provider validation — print raw vision-model output for a captured frame.",
    )
    parser.add_argument("--config", default=None, help="path to config.toml")
    parser.add_argument("--provider", default=None, help="limit to one provider name (e.g. openrouter)")
    parser.add_argument("--region", default=None, help="override capture region as left,top,width,height")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config(args.config)
    if args.region:
        left, top, width, height = (int(part) for part in args.region.split(","))
        cfg = with_region(cfg, left, top, width, height)

    print(f"capturing region {cfg.region}")
    image = grab_region(cfg.region)
    print(f"frame {image.size}, digest {frame_digest(image)}")
    image_b64 = image_to_base64_png(image)

    engine = SignalEngine(market=cfg.market, symbol_hint=cfg.symbol_hint)
    prompt = engine.build_prompt()

    providers = [
        provider
        for provider in make_provider_chain(cfg)
        if args.provider is None or provider.name == args.provider
    ]
    if not providers:
        print(f"no provider matched: {args.provider}", file=sys.stderr)
        return 2

    any_success = False
    for provider in providers:
        print(f"\n===== {provider.name} =====")
        try:
            result = provider.analyze(image_b64, "image/png", prompt)
        except Exception as exc:  # provider may raise ProviderError or network errors
            print(f"FAILED: {type(exc).__name__}: {exc}")
            continue
        print(f"model: {result.model}")
        print("--- raw output ---")
        print(result.text)
        any_success = True

    return 0 if any_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
