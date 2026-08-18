"""Tests for the Vision LLM Layer: NoOp mock, provider payload shapes (via
httpx.MockTransport, no network), and the fallback-chain behavior."""

from __future__ import annotations

import dataclasses
import json
import logging

import httpx
import pytest

from ai_trader.providers import (
    AnthropicProvider,
    NoOpProvider,
    OpenRouterProvider,
    ProviderError,
    ProviderResult,
    VisionClient,
    make_provider_chain,
)
from ai_trader.rate_guard import RateGuard

# Arbitrary base64 "image" — payload shape is what matters, not the content.
PNG = "aGVsbG8="


def test_noop_returns_schema_shaped_json() -> None:
    result = NoOpProvider().analyze(PNG, "image/png", "prompt")
    data = json.loads(result.text)
    assert {"action", "confidence", "reasoning"} <= set(data)
    assert result.model == "noop/mock"


def test_openrouter_requires_key() -> None:
    provider = OpenRouterProvider(api_key="", model="m/free")
    with pytest.raises(ProviderError):
        provider.analyze(PNG, "image/png", "prompt")
    provider.close()


def test_openrouter_payload_is_free_only_and_vision() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "id": "x",
                "model": "m/free",
                "choices": [{"message": {"content": '{"action":"hold","confidence":50}'}}],
            },
        )

    provider = OpenRouterProvider(
        api_key="sk-test",
        model="m/free",
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
    )
    result = provider.analyze(PNG, "image/png", "prompt")

    assert result.model == "m/free"
    assert captured["auth"] == "Bearer sk-test"
    payload = captured["payload"]
    # Free-only hard bound: a paid model can never be charged (plan §2.3).
    assert payload["max_price"] == {"prompt": 0, "completion": 0, "request": 0, "image": 0}
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[1] == {"type": "text", "text": "prompt"}
    provider.close()


def test_anthropic_payload_uses_image_blocks() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        captured["key"] = request.headers.get("x-api-key")
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "type": "message",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": '{"action":"sell","confidence":60}'}],
            },
        )

    provider = AnthropicProvider(
        api_key="sk-ant-test",
        model="claude-sonnet-5",
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
    )
    result = provider.analyze(PNG, "image/png", "prompt")

    assert result.model == "claude-sonnet-5"
    assert captured["key"] == "sk-ant-test"
    content = captured["payload"]["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"] == {"type": "base64", "media_type": "image/png", "data": PNG}
    provider.close()


class _FailingProvider:
    name = "fail"

    def analyze(self, image_b64: str, mime: str, prompt: str) -> None:
        raise ProviderError("boom")


def test_vision_client_falls_forward_to_noop() -> None:
    guard = RateGuard(calls_per_minute=100, min_interval_seconds=0)
    client = VisionClient(
        providers=[_FailingProvider(), NoOpProvider()],
        guard=guard,
        cooldown_seconds=0,
    )
    result = client.analyze(PNG, "image/png", "prompt", chain=["fail", "noop"])
    assert result.model == "noop/mock"


def test_vision_client_raises_when_all_fail() -> None:
    guard = RateGuard(calls_per_minute=100, min_interval_seconds=0)
    client = VisionClient(providers=[_FailingProvider()], guard=guard, cooldown_seconds=0)
    with pytest.raises(ProviderError):
        client.analyze(PNG, "image/png", "prompt")


def test_vision_client_skips_rate_limited_provider() -> None:
    guard = RateGuard(calls_per_minute=1, min_interval_seconds=0)
    noop = NoOpProvider()
    client = VisionClient(providers=[noop], guard=guard, cooldown_seconds=0)

    # First call consumes the only slot; the second must still work via noop...
    assert client.analyze(PNG, "image/png", "prompt").model == "noop/mock"
    # ...but with noop also rate-limited, the chain has nothing left.
    with pytest.raises(ProviderError):
        client.analyze(PNG, "image/png", "prompt", chain=["noop"])


class _CountingProvider:
    """Succeeds every call and counts how often it was asked to analyze."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def analyze(self, image_b64: str, mime: str, prompt: str) -> ProviderResult:
        del image_b64, mime, prompt
        self.calls += 1
        return ProviderResult(text='{"action":"hold","confidence":50}', model=f"{self.name}/model")


def test_rotation_spreads_first_choice_across_providers() -> None:
    a, b = _CountingProvider("a"), _CountingProvider("b")
    guard = RateGuard(calls_per_minute=100, min_interval_seconds=0)
    client = VisionClient(providers=[a, b], guard=guard, cooldown_seconds=0)

    for _ in range(4):
        assert client.analyze(PNG, "image/png", "prompt").model.startswith(("a/", "b/"))
    # Four calls across two real providers -> two serves each (plan §3).
    assert a.calls == 2
    assert b.calls == 2


def test_rotation_never_puts_noop_first() -> None:
    a, b, noop = _CountingProvider("a"), _CountingProvider("b"), NoOpProvider()
    guard = RateGuard(calls_per_minute=100, min_interval_seconds=0)
    client = VisionClient(providers=[a, b, noop], guard=guard, cooldown_seconds=0)

    orders = [client._ordered_chain() for _ in range(4)]
    # noop is the offline last resort and must never rotate to the front.
    assert all(order[-1] == "noop" for order in orders)
    # The two real providers still rotate: a, b, a, b ...
    assert [order[0] for order in orders] == ["a", "b", "a", "b"]


def test_rotation_off_honors_registration_order() -> None:
    a, b = _CountingProvider("a"), _CountingProvider("b")
    guard = RateGuard(calls_per_minute=100, min_interval_seconds=0)
    client = VisionClient(providers=[a, b], guard=guard, cooldown_seconds=0, rotate=False)

    for _ in range(3):
        client.analyze(PNG, "image/png", "prompt")
    assert a.calls == 3
    assert b.calls == 0


def test_rotation_falls_forward_to_the_next_provider() -> None:
    fail, a = _FailingProvider(), _CountingProvider("a")
    guard = RateGuard(calls_per_minute=100, min_interval_seconds=0)
    client = VisionClient(providers=[fail, a], guard=guard, cooldown_seconds=0)

    # Rotated chain starts "fail" first call; the error must fall forward to "a".
    result = client.analyze(PNG, "image/png", "prompt")
    assert result.model == "a/model"
    assert a.calls == 1


def test_provider_chain_falls_through_when_all_cloud_providers_out(caplog) -> None:
    """Phase 6 (outage fallback): with openrouter+anthropic mocked to raise and
    ollama unreachable, the chain must still end on ``noop`` without crashing."""
    with caplog.at_level(logging.WARNING, logger="ai_trader.providers"):
        _drive_outage_chain()
    log_calls = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("falling forward" in line for line in log_calls)


def _drive_outage_chain() -> None:
    """Build a VisionClient over three failing providers + noop and analyze."""

    class _OutageOpenRouter:
        name = "openrouter"

        def analyze(self, image_b64: str, mime: str, prompt: str) -> ProviderResult:
            raise ProviderError("OpenRouter HTTP 503: service unavailable")

    class _OutageAnthropic:
        name = "anthropic"

        def analyze(self, image_b64: str, mime: str, prompt: str) -> ProviderResult:
            raise ProviderError("Anthropic HTTP 503: service unavailable")

    class _UnreachableOllama:
        name = "ollama"

        def analyze(self, image_b64: str, mime: str, prompt: str) -> ProviderResult:
            raise ProviderError("Ollama not running")

    guard = RateGuard(calls_per_minute=100, min_interval_seconds=0)
    client = VisionClient(
        providers=[
            _OutageOpenRouter(),
            _UnreachableOllama(),
            _OutageAnthropic(),
            NoOpProvider(),
        ],
        guard=guard,
        cooldown_seconds=0,
    )

    result = client.analyze(PNG, "image/png", "prompt")

    assert result.model == "noop/mock"


def test_make_provider_chain_full_screen_is_local_only() -> None:
    from ai_trader.config import load_config

    cfg = dataclasses.replace(load_config("does-not-exist.toml"), full_screen=True)
    names = [p.name for p in make_provider_chain(cfg)]
    assert "ollama" in names
    assert "noop" in names
    assert "openrouter" not in names
    assert "anthropic" not in names
