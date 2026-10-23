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
MIN_REWARD_RISK_RATIO = 1.5

DEFAULT_PROMPT_TEMPLATE = """\
You analyze chart screenshots for cautious, short intraday trades only. Return one valid JSON object, no prose or markdown:
{{
  "action": "buy" | "sell" | "hold" | "watch",
  "confidence": 0-100,
  "entry": number or null,
  "stop_loss": number or null,
  "target": number or null,
  "position_size_pct": number or null,
  "timeframe": "visible timeframe or empty",
  "reasoning": "one short sentence",
  "market": "NSE" | "BSE" | "US" | "",
  "symbol": "visible ticker or empty"
}}
Rules:
- If the screenshot is not a trading chart, return "watch".
- Never invent a price, symbol, timeframe, exchange, indicator or pattern. Use null for unreadable levels.
- If the ticker/exchange, timeframe or price levels are unclear, return "watch" or "hold" and explain briefly.
- Intraday only: use a clearly visible 5m/15m or other intraday timeframe. For daily/weekly or unknown timeframe, return "watch".
- Buy only when target > entry > stop_loss; sell only when target < entry < stop_loss. If levels do not clearly support this, use "watch" and null levels.
- Only suggest buy/sell when the visible target offers at least 1.5 times the entry-to-stop distance; otherwise use "watch" and null levels.
- "confidence" is a visual clarity score, not a probability of profit.
- Infer market only from a visible ticker/exchange label. NSE/BSE use INR; US equities use USD. If uncertain, leave market and symbol empty.
Market hint: {market}. Symbol hint: {symbol_hint}. Market notes: {market_notes}"""


def _coerce_price(v: object) -> object:
    """Strip currency symbols, commas, and percent signs from model numbers."""
    if v is None or isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        cleaned = v.strip().strip("$₹€£%~").replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            match = re.search(r"[-+]?\d+(?:\.\d+)?", cleaned)
            if match:
                try:
                    return float(match.group(0))
                except ValueError:
                    return None
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
    quantity: float | None = Field(
        default=None, description="risk-computed units to hold (not from the vision model)"
    )
    currency: str | None = Field(
        default=None, description="currency code (e.g. INR, USD, USDT)"
    )
    timeframe: str = Field(default="", description="e.g. 15m, 1h, 1d")
    reasoning: str = Field(default="", description="brief model rationale")
    market: str = Field(default="", description="NSE | BSE | US")
    symbol: str = Field(default="", description="ticker inferred from the chart")

    @field_validator("action", mode="before")
    @classmethod
    def _coerce_action(cls, v: object) -> object:
        """Strip whitespace, lowercase, and normalize model action synonyms."""
        if isinstance(v, str):
            v_clean = v.strip().lower()
            synonyms = {
                "strong buy": "buy",
                "strong_buy": "buy",
                "strong sell": "sell",
                "strong_sell": "sell",
                "neutral": "hold",
                "wait": "watch",
            }
            return synonyms.get(v_clean, v_clean)
        return v

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

    @field_validator("timeframe", "reasoning", "market", "symbol", "currency", mode="before")
    @classmethod
    def _coerce_none_to_str(cls, v: object) -> object:
        """Allow model responses to use null for optional descriptive fields."""
        return "" if v is None else v

    @field_validator("market", mode="before")
    @classmethod
    def _normalize_market(cls, v: object) -> object:
        """Normalize common TradingView exchange labels to supported market codes."""
        if not isinstance(v, str):
            return v
        raw = v.strip().upper()
        if not raw:
            return ""
        token = raw.split(":", 1)[0].strip()
        compact = " ".join(raw.replace("_", " ").split())
        if token in {"NSE", "NSEI", "NSEINDIA"} or compact in {
            "INDIA", "INDIAN EQUITIES", "NSE CASH", "NSE EQUITIES",
        }:
            return "NSE"
        if token in {"BSE", "BSEINDIA", "BOM"} or compact in {"BSE CASH", "BSE EQUITIES"}:
            return "BSE"
        if token in {
            "NYSE", "NASDAQ", "NASDAQGS", "NASDAQGM", "NASDAQCM", "NYSEARCA",
            "NYSEAMERICAN", "AMEX", "ARCA", "CBOE", "IEX", "US",
        } or compact in {"USA", "UNITED STATES", "US EQUITIES", "US STOCKS", "NYSE/NASDAQ"}:
            return "US"
        return raw

    @field_validator("symbol", mode="before")
    @classmethod
    def _normalize_symbol(cls, v: object) -> object:
        """Remove a recognized exchange prefix (e.g. NASDAQ:TSLA) from a ticker."""
        if not isinstance(v, str):
            return v
        symbol = v.strip().upper()
        if ":" in symbol:
            exchange, ticker = symbol.split(":", 1)
            if exchange in {
                "NSE", "BSE", "NYSE", "NASDAQ", "NASDAQGS", "NASDAQGM",
                "NASDAQCM", "NYSEARCA", "NYSEAMERICAN", "AMEX", "ARCA", "CBOE", "IEX",
            } and ticker.strip():
                return ticker.strip()
        return symbol

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
        ValueError: if output is empty/non-text or no parseable JSON object is found.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("model output is empty or not text")
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
        market: str = "AUTO",
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
        if self.market == "AUTO":
            note_keys = ("NSE", "BSE", "US")
            market_note = "\n".join(
                self.market_notes[key] for key in note_keys if key in self.market_notes
            )
        else:
            market_note = self.market_notes.get(self.market, "")
        return self.prompt_template.format(
            market=self.market,
            symbol_hint=(symbol_hint or self.symbol_hint) or "unknown",
            market_notes=market_note,
        )

    @staticmethod
    def parse(text: str) -> TradingSignal:
        """Parse raw model text into a validated :class:`TradingSignal`."""
        try:
            signal = TradingSignal(**extract_json(text)).validate_action()
        except (ValidationError, ValueError):
            logger.error("signal validation failed on raw text: %s", text)
            raise
        signal.quantity = None  # Ensure model never sets risk quantity
        # Currency is determined by the normalized market, never trusted to the VLM.
        if signal.market in {"NSE", "BSE"}:
            signal.currency = "INR"
        elif signal.market == "US":
            signal.currency = "USD"
        else:
            signal.currency = None
        return _enforce_structure(signal)

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


def _enforce_structure(signal: TradingSignal) -> TradingSignal:
    """Apply deterministic sanity checks to directional model suggestions.

    BUY/SELL levels must have the correct order and at least
    MIN_REWARD_RISK_RATIO potential reward for each unit of price risk.
    Invalid or poor-ratio suggestions are downgraded before they reach sizing,
    the overlay, or the trade logger. If a suggestion is rejected, actionable
    levels are cleared and original values are retained in the reasoning.
    This is a screening heuristic, not a profitability guarantee.
    """
    action = signal.action
    if action not in ("buy", "sell"):
        return signal
    entry, stop, target = signal.entry, signal.stop_loss, signal.target
    if entry is None or stop is None or target is None:
        return signal

    valid = (target > entry > stop) if action == "buy" else (target < entry < stop)
    if not valid:
        marker = f"[STRUCTURALLY INVALID — downgraded from {action}]"
        note = (
            f"original action {action}, entry {entry}, stop_loss {stop}, target {target} "
            f"({marker})"
        )
        signal.action = "hold"
        signal.reasoning = f"{marker} {note}; {signal.reasoning}".strip()
        logger.warning(
            "structurally invalid %s signal downgraded to hold: entry=%s stop=%s target=%s",
            action, entry, stop, target,
        )
        return signal

    risk = (entry - stop) if action == "buy" else (stop - entry)
    reward = (target - entry) if action == "buy" else (entry - target)
    ratio = reward / risk
    if ratio < MIN_REWARD_RISK_RATIO:
        marker = f"[INADEQUATE REWARD/RISK — downgraded from {action}]"
        note = (
            f"original entry {entry}, stop_loss {stop}, target {target}; "
            f"potential reward/risk {ratio:.2f}:1 is below the minimum "
            f"{MIN_REWARD_RISK_RATIO:.2f}:1"
        )
        signal.action = "watch"
        signal.entry = signal.stop_loss = signal.target = None
        signal.position_size_pct = None
        signal.quantity = None
        signal.reasoning = f"{marker} {note}; {signal.reasoning}".strip()
        logger.warning(
            "inadequate reward/risk %.2f:1 downgraded %s signal to watch (minimum %.2f:1)",
            ratio, action, MIN_REWARD_RISK_RATIO,
        )
    return signal
