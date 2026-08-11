from __future__ import annotations

import copy
import os
from pathlib import Path
from unittest import mock

import pytest

from ai_trader.config import DEFAULTS, load_config


@pytest.fixture(autouse=True)
def _no_config_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure load_config() runs against defaults + env only, never a real config.toml."""
    monkeypatch.chdir(tmp_path)
    # Baseline env overrides so the test process's own environment can't leak in.
    for env_name in (
        "AI_TRADER_INTERVAL", "AI_TRADER_MARKET", "AI_TRADER_SYMBOL_HINT",
        "AI_TRADER_PROVIDERS", "AI_TRADER_COOLDOWN", "AI_TRADER_FLIP_HOLD",
        "AI_TRADER_FULL_SCREEN", "AI_TRADER_LOG_HOTKEY", "AI_TRADER_EXCEL_PATH",
        "AI_TRADER_REGION", "AI_TRADER_OPENROUTER_MODEL", "AI_TRADER_OLLAMA_MODEL",
    ):
        monkeypatch.delenv(env_name, raising=False)


def test_deep_merge_returns_independent_copy() -> None:
    """Regression (Task 2): _deep_merge must not share nested dicts with DEFAULTS.

    Loading config and applying env overrides used to mutate the module-level
    DEFAULTS in place (shallow ``dict(base)`` copy), so a later load_config()
    would inherit values set by a previous run.
    """
    load_config()
    assert copy.deepcopy(DEFAULTS) == DEFAULTS
    # The nested capture region is untouched by any mutation during load.
    assert DEFAULTS["capture"]["region"] == {"left": 0, "top": 0, "width": 1280, "height": 720}
    assert DEFAULTS["polling"]["interval_seconds"] == 10
    assert DEFAULTS["providers"]["chain"] == ["ollama", "openrouter", "anthropic", "noop"]


def test_env_override_does_not_leak_between_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression (Task 2): an env override must not stick in DEFAULTS after it's unset."""
    monkeypatch.setenv("AI_TRADER_INTERVAL", "5")
    first = load_config()
    assert first.interval_seconds == 5.0

    monkeypatch.delenv("AI_TRADER_INTERVAL")
    second = load_config()
    assert second.interval_seconds == 10.0  # true default restored, not 5.0
