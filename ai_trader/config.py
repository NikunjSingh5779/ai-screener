"""Configuration loading for AI Trader.

Loads ``config.toml`` (if present) via stdlib ``tomllib``, then applies
environment overrides. No third-party dependencies. All values flow into a
mutable :class:`Config` dataclass.
"""

from __future__ import annotations

import copy
import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

# Sensible defaults so the project runs before the user writes a config.toml.
DEFAULTS: dict = {
    "capture": {
        "region": {"left": 0, "top": 0, "width": 1280, "height": 720},
        "monitor": 1,
        "full_screen": False,
    },
    "polling": {
        "interval_seconds": 10,
        "min_flip_hold_seconds": 60,
    },
    "providers": {
        "chain": ["ollama", "lmstudio", "openrouter", "anthropic", "noop"],
        "cooldown_seconds": 30,
        "ollama_base_url": "http://127.0.0.1:11434",
        "lmstudio_base_url": "http://127.0.0.1:1234/v1",
        "lmstudio_api_key": "",
        "polystrata_base_url": "http://127.0.0.1:8080/v1",
        "polystrata_api_key": "",
        "budget": {"calls_per_minute": 6, "min_interval_seconds": 8},
        "models": {
            "openrouter": "google/gemma-4-31b-it:free",
            "anthropic": "claude-sonnet-5",
            "ollama": "qwen2.5-vl:7b",
            "lmstudio": "local-model",
        },
    },
    "signal": {
        "market": "AUTO",
        "symbol_hint": "",
        "high_confidence_threshold": 80,
        "market_notes": {
            "NSE": (
                "NSE cash market. Currency INR. Typical hours 09:15-15:30 IST. "
                "Do not assume F&O lot sizes on a cash-market chart."
            ),
            "BSE": (
                "BSE cash market. Currency INR. Typical hours 09:15-15:30 IST. "
                "Do not assume F&O lot sizes on a cash-market chart."
            ),
            "US": (
                "US-listed equities. Currency USD ($). Regular session is generally 09:30-16:00 America/New_York, "
                "but the captured chart may show pre-market or after-hours. Confirm the exchange and session "
                "from visible chart data; never mix regular and extended sessions silently."
            ),
            "AUTO": (
                "The chart may be an Indian cash equity (NSE/BSE, INR) or a US-listed equity (USD). "
                "Infer market only from a visible exchange/symbol label. NSE/BSE regular cash session is "
                "typically 09:15-15:30 Asia/Kolkata; US regular session is typically 09:30-16:00 America/New_York. "
                "If exchange, symbol, currency or session cannot be identified reliably, return watch and say why. "
                "Do not assume US pre-market/after-hours or Indian derivatives."
            ),
            "Crypto": (
                "Cryptocurrency trades 24/7 - ignore session-hours reasoning. "
                "High volatility. Advisory only."
            ),
        },
    },
    "risk": {
        "account_size": 0.0,  # legacy single-currency setting; interpreted using account_currency
        "account_currency": "INR",
        "account_size_inr": 0.0,
        "account_size_usd": 0.0,
        "risk_per_trade_pct": 0.5,
    },
    "logging": {
        "hotkey": "f9",
        "excel_path": "Trade_Log_Tracker.xlsx",
    },
}

_ENV_OVERRIDES = {
    "AI_TRADER_INTERVAL": ("polling", "interval_seconds"),
    "AI_TRADER_MARKET": ("signal", "market"),
    "AI_TRADER_SYMBOL_HINT": ("signal", "symbol_hint"),
    "AI_TRADER_PROVIDERS": ("providers", "chain"),
    "AI_TRADER_COOLDOWN": ("providers", "cooldown_seconds"),
    "AI_TRADER_FLIP_HOLD": ("polling", "min_flip_hold_seconds"),
    "AI_TRADER_FULL_SCREEN": ("capture", "full_screen"),
    "AI_TRADER_LOG_HOTKEY": ("logging", "hotkey"),
    "AI_TRADER_EXCEL_PATH": ("logging", "excel_path"),
    "AI_TRADER_HIGH_CONFIDENCE": ("signal", "high_confidence_threshold"),
    "AI_TRADER_ACCOUNT_SIZE": ("risk", "account_size"),
    "AI_TRADER_ACCOUNT_SIZE_INR": ("risk", "account_size_inr"),
    "AI_TRADER_ACCOUNT_SIZE_USD": ("risk", "account_size_usd"),
    "AI_TRADER_ACCOUNT_CURRENCY": ("risk", "account_currency"),
    "AI_TRADER_OLLAMA_BASE_URL": ("providers", "ollama_base_url"),
    "AI_TRADER_LMSTUDIO_BASE_URL": ("providers", "lmstudio_base_url"),
    "AI_TRADER_LMSTUDIO_API_KEY": ("providers", "lmstudio_api_key"),
    "AI_TRADER_POLYSTRATA_BASE_URL": ("providers", "polystrata_base_url"),
    "AI_TRADER_POLYSTRATA_API_KEY": ("providers", "polystrata_api_key"),
    "AI_TRADER_RISK_PCT": ("risk", "risk_per_trade_pct"),
}


@dataclass(frozen=True)
class Region:
    left: int
    top: int
    width: int
    height: int

    @property
    def nonzero(self) -> bool:
        return self.width > 0 and self.height > 0

    def as_mss(self) -> dict[str, int]:
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}


@dataclass
class Config:
    region: Region
    monitor: int
    full_screen: bool
    interval_seconds: float
    min_flip_hold_seconds: float
    provider_chain: list[str]
    cooldown_seconds: float
    ollama_base_url: str
    lmstudio_base_url: str
    lmstudio_api_key: str
    calls_per_minute: int
    min_interval_seconds: float
    model_openrouter: str
    model_anthropic: str
    model_ollama: str
    model_lmstudio: str
    model_polystrata: str
    polystrata_base_url: str
    polystrata_api_key: str
    market: str
    symbol_hint: str
    market_notes: dict[str, str]
    high_confidence_threshold: float
    account_size: float
    account_currency: str
    account_size_inr: float
    account_size_usd: float
    risk_per_trade_pct: float
    log_hotkey: str
    excel_path: str


def _deep_merge(base: dict, override: dict) -> dict:
    # Deep-copy the base so nested dicts are independent of the caller's data —
    # otherwise later env overrides mutate the shared module-level DEFAULTS.
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _apply_env_overrides(data: dict) -> dict:
    for env_name, (section, key) in _ENV_OVERRIDES.items():
        value = os.getenv(env_name)
        if value is None:
            continue
        if key == "chain":
            data[section][key] = [p.strip() for p in value.split(",") if p.strip()]
        elif key == "full_screen":
            data[section][key] = value.strip().lower() in ("1", "true", "yes", "on")
        elif key in ("interval_seconds", "min_flip_hold_seconds", "cooldown_seconds",
                     "account_size", "account_size_inr", "account_size_usd",
                     "risk_per_trade_pct", "high_confidence_threshold"):
            data[section][key] = float(value)
        else:
            data[section][key] = value
    if os.getenv("AI_TRADER_REGION"):
        left, top, width, height = (int(x) for x in os.getenv("AI_TRADER_REGION").split(","))
        data["capture"]["region"] = {"left": left, "top": top, "width": width, "height": height}
    openrouter_model = os.getenv("AI_TRADER_OPENROUTER_MODEL") or os.getenv("OPENROUTER_MODEL")
    if openrouter_model:
        data["providers"]["models"]["openrouter"] = openrouter_model
    anthropic_model = os.getenv("AI_TRADER_ANTHROPIC_MODEL") or os.getenv("ANTHROPIC_MODEL")
    if anthropic_model:
        data["providers"]["models"]["anthropic"] = anthropic_model
    ollama_model = os.getenv("AI_TRADER_OLLAMA_MODEL") or os.getenv("OLLAMA_MODEL")
    if ollama_model:
        data["providers"]["models"]["ollama"] = ollama_model
    lmstudio_model = os.getenv("AI_TRADER_LMSTUDIO_MODEL") or os.getenv("LMSTUDIO_MODEL")
    if lmstudio_model:
        data["providers"]["models"]["lmstudio"] = lmstudio_model
    return data


def load_config(path: str | Path | None = None) -> Config:
    """Load config from ``config.toml`` (or ``path``) over defaults + env vars."""
    data = _deep_merge(DEFAULTS, {})
    cfg_path = Path(path) if path else Path.cwd() / "config.toml"
    if cfg_path.exists():
        with open(cfg_path, "rb") as handle:
            data = _deep_merge(data, tomllib.load(handle))
    data = _apply_env_overrides(data)

    region = data["capture"]["region"]
    models = data["providers"]["models"]
    budget = data["providers"]["budget"]
    return Config(
        region=Region(
            left=region["left"], top=region["top"],
            width=region["width"], height=region["height"],
        ),
        monitor=data["capture"]["monitor"],
        full_screen=bool(data["capture"]["full_screen"]),
        interval_seconds=float(data["polling"]["interval_seconds"]),
        min_flip_hold_seconds=float(data["polling"]["min_flip_hold_seconds"]),
        provider_chain=list(data["providers"]["chain"]),
        cooldown_seconds=float(data["providers"]["cooldown_seconds"]),
        ollama_base_url=str(data["providers"].get("ollama_base_url", "http://127.0.0.1:11434")),
        lmstudio_base_url=str(data["providers"].get("lmstudio_base_url", "http://127.0.0.1:1234/v1")),
        lmstudio_api_key=str(data["providers"].get("lmstudio_api_key", "")),
        calls_per_minute=int(budget["calls_per_minute"]),
        min_interval_seconds=float(budget["min_interval_seconds"]),
        model_openrouter=str(models["openrouter"]),
        model_anthropic=str(models["anthropic"]),
        model_ollama=str(models["ollama"]),
        model_lmstudio=str(models.get("lmstudio", "local-model")),
        model_polystrata=str(models.get("polystrata", "strata")),
        polystrata_base_url=str(data["providers"].get("polystrata_base_url", "http://127.0.0.1:8080/v1")),
        polystrata_api_key=str(data["providers"].get("polystrata_api_key", "")),
        market=str(data["signal"]["market"]).strip().upper(),
        symbol_hint=str(data["signal"]["symbol_hint"]),
        market_notes=dict(data["signal"].get("market_notes", {})),
        high_confidence_threshold=float(data["signal"].get("high_confidence_threshold", 80)),
        account_size=float(data["risk"].get("account_size", 0.0)),
        account_currency=str(data["risk"].get("account_currency", "INR")).upper(),
        account_size_inr=float(data["risk"].get("account_size_inr", 0.0)),
        account_size_usd=float(data["risk"].get("account_size_usd", 0.0)),
        risk_per_trade_pct=float(data["risk"].get("risk_per_trade_pct", 0.5)),
        log_hotkey=str(data["logging"]["hotkey"]),
        excel_path=str(data["logging"]["excel_path"]),
    )


def with_region(cfg: Config, left: int, top: int, width: int, height: int) -> Config:
    """Return a copy of ``cfg`` with a new capture region (immutable pattern)."""
    return replace(cfg, region=Region(left=left, top=top, width=width, height=height))


def account_size_for_market(cfg: Config, market: str) -> float:
    """Return capital expressed in the instrument's quote currency.

    New configurations should set separate INR and USD balances. The legacy
    single account_size value is only used when its declared account_currency
    matches the detected instrument currency; no FX conversion is guessed.
    """
    normalized_market = str(market or "").strip().upper()
    if normalized_market in {"NSE", "BSE"}:
        if cfg.account_size_inr > 0:
            return cfg.account_size_inr
        if cfg.account_currency == "INR":
            return cfg.account_size
        return 0.0
    if normalized_market == "US":
        if cfg.account_size_usd > 0:
            return cfg.account_size_usd
        if cfg.account_currency == "USD":
            return cfg.account_size
        return 0.0
    return 0.0
