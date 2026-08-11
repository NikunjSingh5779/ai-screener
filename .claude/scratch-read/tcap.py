"""Tests for capture helpers and config: frame digest determinism, base64 PNG
encoding, config defaults, and env/TOML overrides."""

from __future__ import annotations

import base64

from PIL import Image

from ai_trader.capture import frame_digest, image_to_base64_png
from ai_trader.config import Region, load_config

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _solid(color: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGB", (64, 48), color)


def test_frame_digest_is_stable_for_same_frame() -> None:
    assert frame_digest(_solid((255, 0, 0))) == frame_digest(_solid((255, 0, 0)))


def test_frame_digest_changes_for_different_frame() -> None:
    assert frame_digest(_solid((255, 0, 0))) != frame_digest(_solid((0, 0, 255)))


def test_image_to_base64_png_encodes_valid_png() -> None:
    encoded = image_to_base64_png(_solid((10, 20, 30)))
    assert encoded
    assert base64.b64decode(encoded)[:8] == PNG_MAGIC


def test_config_defaults_load_without_file() -> None:
    cfg = load_config("does-not-exist.toml")
    assert cfg.region.width == 1280
    assert cfg.provider_chain == ["ollama", "openrouter", "anthropic", "noop"]
    assert cfg.calls_per_minute == 6


def test_config_reads_toml(tmp_path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[capture]\n"
        'region = { left = 100, top = 50, width = 900, height = 600 }\n'
        "\n[polling]\n"
        "interval_seconds = 5\n",
        encoding="utf-8",
    )
    cfg = load_config(config_file)
    assert cfg.region.left == 100
    assert cfg.region.width == 900
    assert cfg.interval_seconds == 5
    # Unspecified values keep their defaults (deep-merge).
    assert cfg.calls_per_minute == 6


def test_env_region_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AI_TRADER_REGION", "10,20,300,200")
    cfg = load_config(tmp_path / "none.toml")
    assert (cfg.region.left, cfg.region.top, cfg.region.width, cfg.region.height) == (10, 20, 300, 200)


def test_region_nonzero() -> None:
    assert Region(0, 0, 1280, 720).nonzero
    assert not Region(0, 0, 0, 0).nonzero
