"""Signal Engine — turns a vision-model response into a strict structured signal.

Builds the analysis prompt, parses model text into a :class:`TradingSignal`
(defensively: fenced / embedded / verbose JSON), and keeps a short per-symbol
rolling memory plus a flip-flop guard so ordinary chart noise cannot make the
call oscillate buy→sell→buy (plan §2.4, §6).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

VALID_ACTIONS = {"buy", "sell", "hold", "watch"}

DEFAULT_PROMPT_TEMPLATE = """\
You are a trading analyst. The image is a chart screenshot from a trading platform.
Analyze it and answer ONLY with a single JSON object — no prose, no markdown fences.

Schema (use null when a level is not visible on the chart; never guess):
{{
  "action": "buy" | "sell" | "hold" | "watch",
  "confidence": <0-100 number>,
  "entry": <number or null>,
  "stop_loss": <number or null>,
  "target": <number or null>,
  "position_size_pct": <0-100 number or null>,
  "timeframe": "<e.g. 15m, 1h, 1d>",
  "reasoning": "<1-2 sentence rationale>",
  "market": "<NSE | BSE | US | Crypto, infer from the chart>",
  "symbol": "<ticker inferred from the chart>"
}}

Guidelines:
- Base every number on what is visible on the chart; use null if a level is not
  visible rather than guessing.
- "confidence" reflects how clearly the setup reads; prefer honest mid values.
- Advisory only — never imply order execution.

Market hint: {market}. Symbol hint: {symbol_hint}.

Market notes: {market_notes}"""


def _coerce_price(v: object) -> object:
    """Strip currency symbols, commas, and percent signs from model numbers."""
    if v is None or isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        cleaned = v.strip().strip("$₹%").replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return v


class TradingSignal(BaseModel):
    """The strict signal schema the vision model is asked to produce."""

    action: str = Field(description="buy | sell | hold | watch")
    confidence: float = Field(ge=0, le=100, description="0-100")
    entry: float | None = Field(default=None, description="suggested entry price")
    stop_loss: float | None = Field(default=None, description="stop-loss price")
    target: float | None = Field(default=None, description="target price")
    position_size_pct: float | None = Field(
        default=None, ge=0, le=100, description="% of capital to risk"
    )
    timeframe: str = Field(default="", description="e.g. 15m, 1h, 1d")
    reasoning: str = Field(default="", description="brief model rationale")
    market: str = Field(default="", description="NSE | BSE | US | Crypto")
    symbol: str = Field(default="", description="ticker inferred from the chart")

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v: object) -> object:
        """Free vision models sometimes send a % string, a word rating, or null."""
        if v is None:
            return 50.0
        if isinstance(v, str):
            v = v.strip().rstrip("%").strip()
            word_map = {"low": 25.0, "medium": 50.0, "moderate": 50.0, "high": 75.0}
            if v.lower() in word_map:
                return word_map[v.lower()]
            try:
                return float(v)
            except ValueError:
                return 50.0
        return v

    @field_validator("entry", "stop_loss", "target", "position_size_pct", mode="before")
    @classmethod
    def _coerce_prices(cls, v: object) -> object:
        return _coerce_price(v)

    @field_validator("timeframe", "reasoning", "market", "symbol", mode="before")
    @classmethod
    def _coerce_none_to_str(cls, v: object) -> object:
        """Allow model responses to use null for optional descriptive fields."""
        return "" if v is None else v

    def validate_action(self) -> TradingSignal:
        """Ensure the model returned one of the supported signal actions."""
        if self.action not in VALID_ACTIONS:
            raise ValueError(f"unsupported signal action: {self.action!r}")
        return self


@dataclass
class SignalContext:
    symbol: str
    signal: TradingSignal
    captured_at: float
    provider: str
    model: str


def extract_json(text: str) -> dict:
    """Extract a JSON object from model output that may be fenced or verbose.

    Raises:
        ValueError: if no parseable JSON object is found.
    """
    cleaned = text.strip()
    # Strip one code fence if present (```json ... ```).
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    start = cleaned.find("{")
    if start == -1:
        raise ValueError("no JSON object found in model output")

    # Fast path: the remainder from the first brace parses as JSON.
    try:
        return json.loads(cleaned[start:])
    except json.JSONDecodeError:
        pass

    # Fallback: brace-match the first top-level object.
    depth = 0
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start : index + 1])
    raise ValueError("unbalanced JSON in model output")


class SignalEngine:
    """Builds prompts, parses model text into signals, guards against flip-flops."""

    def __init__(
        self,
        market: str = "NSE",
        symbol_hint: str = "",
        market_notes: dict[str, str] | None = None,
        min_flip_hold_seconds: float = 60.0,
        prompt_template: str = DEFAULT_PROMPT_TEMPLATE,
    ) -> None:
        self.market = market
        self.symbol_hint = symbol_hint
        self.market_notes = market_notes or {}
        self.min_flip_hold_seconds = min_flip_hold_seconds
        self.prompt_template = prompt_template
        # symbol -> (last directional action, wall-clock time it was seen)
        self._last_direction: dict[str, tuple[str, float]] = {}

    def build_prompt(self, symbol_hint: str | None = None) -> str:
        return self.prompt_template.format(
            market=self.market,
            symbol_hint=(symbol_hint or self.symbol_hint) or "unknown",
            market_notes=self.market_notes.get(self.market, ""),
        )

    @staticmethod
    def parse(text: str) -> TradingSignal:
        """Parse raw model text into a validated :class:`TradingSignal`."""
        try:
            return TradingSignal(**extract_json(text)).validate_action()
        except (ValidationError, ValueError):
            logger.error("signal validation failed on raw text: %s", text)
            raise

    def guard_flip(self, symbol: str, signal: TradingSignal, now: float | None = None) -> bool:
        """True if ``signal`` may be shown; False if it flips direction too soon.

        Only directional actions (buy/sell) are tracked. A flip back is suppressed
        within ``min_flip_hold_seconds`` of the last directional call — the
        plan's guard against buy→sell→buy on ordinary chart noise.
        """
        if signal.action not in ("buy", "sell"):
            return True
        now = now if now is not None else time.time()
        previous = self._last_direction.get(symbol)
        if previous is not None:
            prev_action, prev_at = previous
            if prev_action in ("buy", "sell") and prev_action != signal.action:
                if now - prev_at < self.min_flip_hold_seconds:
                    logger.info(
                        "flip suppressed for %s (%s -> %s within %.0fs)",
                        symbol, prev_action, signal.action, now - prev_at,
                    )
                    return False
        self._last_direction[symbol] = (signal.action, now)
        return True
