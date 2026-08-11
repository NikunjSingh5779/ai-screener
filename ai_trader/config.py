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
        "chain": ["ollama", "openrouter", "anthropic", "noop"],
        "cooldown_seconds": 30,
        "budget": {"calls_per_minute": 6, "min_interval_seconds": 8},
        "models": {
            "openrouter": "google/gemma-4-31b-it:free",
            "anthropic": "claude-sonnet-5",
            "ollama": "moondream",
        },
    },
    "signal": {"market": "NSE", "symbol_hint": ""},
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
    calls_per_minute: int
    min_interval_seconds: float
    model_openrouter: str
    model_anthropic: str
    model_ollama: str
    market: str
    symbol_hint: str
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
        elif key in ("interval_seconds", "min_flip_hold_seconds", "cooldown_seconds"):
            data[section][key] = float(value)
        else:
            data[section][key] = value
    if os.getenv("AI_TRADER_REGION"):
        left, top, width, height = (int(x) for x in os.getenv("AI_TRADER_REGION").split(","))
        data["capture"]["region"] = {"left": left, "top": top, "width": width, "height": height}
    if os.getenv("AI_TRADER_OPENROUTER_MODEL"):
        data["providers"]["models"]["openrouter"] = os.getenv("AI_TRADER_OPENROUTER_MODEL")
    if os.getenv("AI_TRADER_OLLAMA_MODEL"):
        data["providers"]["models"]["ollama"] = os.getenv("AI_TRADER_OLLAMA_MODEL")
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
        calls_per_minute=int(budget["calls_per_minute"]),
        min_interval_seconds=float(budget["min_interval_seconds"]),
        model_openrouter=str(models["openrouter"]),
        model_anthropic=str(models["anthropic"]),
        model_ollama=str(models["ollama"]),
        market=str(data["signal"]["market"]),
        symbol_hint=str(data["signal"]["symbol_hint"]),
        log_hotkey=str(data["logging"]["hotkey"]),
        excel_path=str(data["logging"]["excel_path"]),
    )


def with_region(cfg: Config, left: int, top: int, width: int, height: int) -> Config:
    """Return a copy of ``cfg`` with a new capture region (immutable pattern)."""
    return replace(cfg, region=Region(left=left, top=top, width=width, height=height))
