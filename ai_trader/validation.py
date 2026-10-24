"""Deterministic, model-independent safety checks for advisory signals."""
from __future__ import annotations
import math
import re
from collections.abc import Iterable

SUPPORTED_MARKETS = frozenset({"NSE", "BSE", "US"})
CRYPTO_EXCHANGES = frozenset({"BINANCE", "COINBASE", "KRAKEN", "BITSTAMP", "BYBIT", "OKX", "BITFINEX", "KUCOIN", "GEMINI", "CRYPTO"})
CRYPTO_BASE_TICKERS = frozenset({"BTC","ETH","BNB","SOL","XRP","ADA","DOGE","AVAX","DOT","LINK","LTC","BCH","XLM","TRX","TON","SHIB","PEPE","SUI","APT","NEAR","ICP","ETC","FIL","ARB","OP","UNI","AAVE","MKR","ATOM","XMR","ALGO","VET","INJ","SEI","FET"})
CRYPTO_QUOTES = ("USDT","USDC","BUSD","FDUSD","TUSD","USD","EUR","GBP","BTC","ETH","BNB")
MIN_REWARD_RISK_RATIO = 1.5
DEFAULT_MAX_DATA_AGE_SECONDS = 60.0

def looks_like_crypto_symbol(symbol: str) -> bool:
    """Recognize common crypto exchange labels and common base/quote pairs."""
    raw = str(symbol or "").strip().upper()
    if not raw: return False
    if ":" in raw:
        exchange, raw = raw.split(":", 1)
        if exchange.strip() in CRYPTO_EXCHANGES: return True
    ticker = re.sub(r"[^A-Z0-9]", "", raw)
    if ticker in CRYPTO_BASE_TICKERS: return True
    return any(ticker.endswith(q) and ticker[:-len(q)] in CRYPTO_BASE_TICKERS for q in CRYPTO_QUOTES)

def validate_signal_safety(*, action: str, market: str, symbol: str, entry: float | None,
    stop_loss: float | None, target: float | None, expected_market: str = "AUTO",
    symbol_hint: str = "", data_age_seconds: float | None = None,
    freshness_verified: bool | None = None,
    max_data_age_seconds: float = DEFAULT_MAX_DATA_AGE_SECONDS) -> list[str]:
    """Return deterministic safety failures. Empty means supplied checks passed.

    Screenshot capture time is not evidence of market-data freshness. A caller must
    pass timestamp-derived evidence; unknown age is not falsely labeled stale.
    """
    action=str(action or "").strip().lower(); market=str(market or "").strip().upper()
    symbol=str(symbol or "").strip().upper(); expected_market=str(expected_market or "AUTO").strip().upper()
    symbol_hint=str(symbol_hint or "").strip().upper(); errors=[]
    if action not in {"buy","sell"}: return errors
    if market not in SUPPORTED_MARKETS: errors.append("market/exchange is unknown or unsupported")
    if not symbol: errors.append("symbol identity is missing")
    elif looks_like_crypto_symbol(symbol): errors.append("cryptocurrency pairs are outside the supported equities scope")
    if expected_market != "AUTO" and market != expected_market:
        errors.append(f"signal market {market or 'unknown'} conflicts with configured market {expected_market}")
    if symbol_hint and symbol and symbol != symbol_hint:
        errors.append(f"signal symbol {symbol} conflicts with configured symbol hint {symbol_hint}")
    levels=(entry,stop_loss,target)
    if any(v is None for v in levels):
        errors.append("entry, stop-loss, and target must all be present")
    else:
        try: finite=all(math.isfinite(float(v)) for v in levels)
        except (TypeError,ValueError,OverflowError): finite=False
        if not finite: errors.append("entry, stop-loss, and target must be finite numbers")
        else:
            e,s,t=(float(v) for v in levels)
            ordered=(s<e<t) if action=="buy" else (t<e<s)
            risk=(e-s) if action=="buy" else (s-e)
            reward=(t-e) if action=="buy" else (e-t)
            if not ordered: errors.append(f"{action.upper()} levels have invalid ordering")
            elif risk<=0 or reward/risk<MIN_REWARD_RISK_RATIO:
                errors.append(f"reward/risk is below the minimum {MIN_REWARD_RISK_RATIO:.1f}:1")
    if freshness_verified is False: errors.append("market-data freshness is explicitly unverified")
    if data_age_seconds is not None:
        try:
            age=float(data_age_seconds); max_age=float(max_data_age_seconds)
            if not math.isfinite(age) or age<0 or not math.isfinite(max_age) or max_age<0:
                errors.append("market-data age metadata is invalid")
            elif age>max_age: errors.append(f"market data is stale ({age:g}s > {max_age:g}s)")
            elif freshness_verified is not True:
                errors.append("market-data age was supplied but freshness was not verified")
        except (TypeError,ValueError,OverflowError): errors.append("market-data age metadata is invalid")
    return errors

def downgrade_unsafe_signal(signal, errors: Iterable[str]):
    """Fail closed for directional actions and retain an audit explanation."""
    failures=list(errors)
    if failures and str(getattr(signal,"action","")).lower() in {"buy","sell"}:
        original=signal.action; signal.action="watch"
        signal.entry=signal.stop_loss=signal.target=None
        if hasattr(signal,"position_size_pct"): signal.position_size_pct=None
        if hasattr(signal,"quantity"): signal.quantity=None
        signal.reasoning=(f"NO TRADE: deterministic safety guard blocked {original.upper()}: "
            + "; ".join(failures)
            + (f". Model rationale: {signal.reasoning}" if signal.reasoning else ""))
    return signal
