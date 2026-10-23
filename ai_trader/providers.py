"""Vision LLM Layer — provider-agnostic backends with an ordered fallback chain.

Backends (plan §2.3):
  - ``noop``        : deterministic offline mock, $0, powers tests/dev.
  - ``openrouter``  : free vision models via OpenRouter; ``max_price`` is
                      hard-zeroed so a paid model can never be charged
                      (same guard as ARES ``agents/client.py``).
  - ``anthropic``   : Messages API via httpx (vision image blocks).

:class:`VisionClient` resolves an analysis through the configured chain, skipping
providers on cooldown or over budget (via :class:`ai_trader.rate_guard.RateGuard`)
and falling forward on throttle/error. It reports which provider/model served each
result so a quality drop is traceable, never mysterious (plan §6).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import BaseModel

from ai_trader.config import Config
from ai_trader.rate_guard import RateGuard

logger = logging.getLogger(__name__)

OPENROUTER_DEFAULT_URL = "https://openrouter.ai/api/v1"
ANTHROPIC_DEFAULT_URL = "https://api.anthropic.com"
LMSTUDIO_DEFAULT_URL = "http://localhost:1234/v1"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 1024


def _load_dotenv(path: str | Path | None = None) -> bool:
    """Load ``KEY=VALUE`` pairs from a ``.env`` file into ``os.environ``
    without clobbering already-set variables.

    Default path is ``<project root>/.env``. Stdlib-only on purpose — the
    project keeps zero non-core dependencies; a flat file needs no library.
    Called once at import so every provider/config env read sees the file.
    """
    env_path = Path(path) if path else Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return False
    loaded = 0
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    if loaded:
        logger.info("loaded %d variable(s) from %s", loaded, env_path)
    return loaded > 0


_load_dotenv()  # project-root .env — before any provider reads its env vars


class ProviderError(RuntimeError):
    """A provider failed in a way the fallback chain should recover from."""


@dataclass
class ProviderResult:
    text: str
    model: str


class Provider(Protocol):
    name: str

    def analyze(self, image_b64: str, mime: str, prompt: str) -> ProviderResult:
        """Send ``image_b64`` (a base64 PNG string) plus ``prompt`` and return the
        model's raw text. Raises :class:`ProviderError` on any failure."""


class NoOpProvider:
    """Deterministic offline mock. Never represents real market analysis."""

    name = "noop"

    def analyze(self, image_b64: str, mime: str, prompt: str) -> ProviderResult:
        del image_b64, mime, prompt
        return ProviderResult(
            text=json.dumps(
                {
                    "action": "hold",
                    "confidence": 0,
                    "entry": None,
                    "stop_loss": None,
                    "target": None,
                    "position_size_pct": None,
                    "timeframe": "",
                    "reasoning": (
                        "TEST MODE ONLY: no vision model produced this output. "
                        "Do not interpret as a market signal or trade."
                    ),
                    "market": "",
                    "symbol": "",
                }
            ),
            model="noop/mock",
        )


class OpenRouterProvider:
    """OpenRouter free vision models.

    Free-only: ``max_price`` is pinned to zero so the provider hard-rejects any
    model that would cost money (returns 402) instead of silently charging.
    """

    name = "openrouter"
    default_base_url = OPENROUTER_DEFAULT_URL

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.base_url = (base_url or os.getenv("OPENROUTER_BASE_URL") or self.default_base_url).rstrip("/")
        self.model = model or os.getenv("OPENROUTER_MODEL") or ""
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def analyze(self, image_b64: str, mime: str, prompt: str) -> ProviderResult:
        if not self.api_key:
            raise ProviderError("OpenRouter has no API key (set OPENROUTER_API_KEY)")
        if not self.model:
            raise ProviderError("OpenRouter has no model configured")
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost",  # OpenRouter requires these
            "X-Title": "AI Trader",
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": DEFAULT_MAX_TOKENS,
            "max_price": {"prompt": 0, "completion": 0, "request": 0, "image": 0},
        }
        try:
            response = self._client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"OpenRouter HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"OpenRouter request failed: {exc}") from exc

        if isinstance(data, dict) and "error" in data:
            raise ProviderError(f"OpenRouter returned an error: {data['error']}")
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"OpenRouter response missing content: {data}") from exc
        served_model = data.get("model", self.model)
        return ProviderResult(text=text, model=served_model)

    def close(self) -> None:
        self._client.close()


class AnthropicProvider:
    """Anthropic Messages API via httpx (vision image blocks).

    Reads ``ANTHROPIC_API_KEY`` (project use) and optional ``ANTHROPIC_BASE_URL``.
    """

    name = "anthropic"
    default_base_url = ANTHROPIC_DEFAULT_URL

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.base_url = (base_url or os.getenv("ANTHROPIC_BASE_URL") or self.default_base_url).rstrip("/")
        self.model = model or os.getenv("ANTHROPIC_MODEL") or ""
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def analyze(self, image_b64: str, mime: str, prompt: str) -> ProviderResult:
        if not self.api_key:
            raise ProviderError("Anthropic has no API key (set ANTHROPIC_API_KEY)")
        if not self.model:
            raise ProviderError("Anthropic has no model configured")
        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": mime, "data": image_b64},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }
        try:
            response = self._client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"Anthropic HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Anthropic request failed: {exc}") from exc

        try:
            text = "".join(
                block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
            )
        except (AttributeError, TypeError) as exc:
            raise ProviderError(f"Anthropic response missing text: {data}") from exc
        if not text:
            raise ProviderError(f"Anthropic returned no text content: {data}")
        served_model = data.get("model", self.model)
        return ProviderResult(text=text, model=served_model)

    def close(self) -> None:
        self._client.close()


class OllamaProvider:
    """Local vision model via Ollama — zero API keys, zero rate limits.

    Talks to ``http://localhost:11434`` (the default Ollama server) using the
    ``/api/chat`` endpoint with image support. Install Ollama, pull a vision
    model (``ollama pull moondream``), and this provider Just Works offline.
    """

    name = "ollama"
    default_base_url = "http://localhost:11434"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.model = model or os.getenv("OLLAMA_MODEL") or "qwen2.5-vl:7b"
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or self.default_base_url).rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    def analyze(self, image_b64: str, mime: str, prompt: str) -> ProviderResult:
        del mime  # Ollama accepts raw base64 regardless of mime type
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64],
                }
            ],
        }
        try:
            response = self._client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.ConnectError as exc:
            raise ProviderError(
                "Ollama not running — start it with 'ollama serve' or install from ollama.com"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"Ollama HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Ollama request failed: {exc}") from exc

        try:
            text = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"Ollama response missing content: {data}") from exc
        served_model = data.get("model", self.model)
        return ProviderResult(text=text, model=f"ollama/{served_model}")

    def close(self) -> None:
        self._client.close()


class LMStudioProvider:
    """Local vision model via LM Studio / llama.cpp / vLLM (OpenAI-compatible).

    Talks to ``http://localhost:1234/v1`` (the default LM Studio local server)
    using the ``/chat/completions`` endpoint with vision image_url support.
    Runs locally on GPU (RTX 5070 Ti) with zero API spend and zero cloud leakage.
    """

    name = "lmstudio"
    default_base_url = LMSTUDIO_DEFAULT_URL

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model or os.getenv("AI_TRADER_LMSTUDIO_MODEL") or os.getenv("LMSTUDIO_MODEL") or "local-model"
        self.base_url = (base_url or os.getenv("AI_TRADER_LMSTUDIO_BASE_URL") or os.getenv("LMSTUDIO_BASE_URL") or self.default_base_url).rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def analyze(self, image_b64: str, mime: str, prompt: str) -> ProviderResult:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": DEFAULT_MAX_TOKENS,
            "temperature": 0.1,
        }
        try:
            response = self._client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.ConnectError as exc:
            raise ProviderError(
                "LM Studio not running — start it, load a vision model, and enable the local server at http://localhost:1234"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"LM Studio HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"LM Studio request failed: {exc}") from exc

        if isinstance(data, dict) and "error" in data:
            raise ProviderError(f"LM Studio returned an error: {data['error']}")
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"LM Studio response missing content: {data}") from exc
        served_model = data.get("model", self.model)
        return ProviderResult(text=text, model=f"lmstudio/{served_model}")

    def close(self) -> None:
        self._client.close()


def _base_models() -> dict[str, type]:
    """Registry used by :func:`make_provider_chain`; keys match config provider names."""
    return {
        "noop": NoOpProvider,
        "openrouter": OpenRouterProvider,
        "anthropic": AnthropicProvider,
        "ollama": OllamaProvider,
        "lmstudio": LMStudioProvider,
    }


_CLOUD_PROVIDERS = frozenset({"openrouter", "anthropic"})
_LOCAL_PROVIDERS = frozenset({"ollama", "lmstudio"})


def _cloud_keys_present() -> list[str]:
    """Names of cloud providers that have a key in the environment.

    Used by :func:`make_provider_chain` to decide whether cloud providers are
    usable at all: a cloud first choice only makes sense when its key exists
    (the key's absence used to be discovered only mid-call, wasting a read on
    a guaranteed failure).
    """
    present = []
    if os.getenv("OPENROUTER_API_KEY"):
        present.append("openrouter")
    if os.getenv("ANTHROPIC_API_KEY"):
        present.append("anthropic")
    return present


def _order_chain(cfg: Config) -> list[str]:
    """Resolve the effective provider chain for this run.

    Ordering rules (the key rule is what makes OpenRouter actually get used):
      * When a cloud provider has a key, it moves to the head — the keyed
        cloud is the first choice, and every provider after it is fallback.
      * ``AI_TRADER_SKIP_LOCAL=1`` removes the local providers (ollama) and
        any cloud provider without a key, so a validation run can prove a
        specific keyed cloud provider (Task 3 mode).
      * ``full_screen`` removes cloud providers entirely — a full-desktop
        frame never leaves the machine (plan §2.1 privacy guard).
    ``noop`` is always guaranteed last (offline last resort).
    """
    names: list[str] = []
    for name in cfg.provider_chain:
        if name == "noop":
            continue
        if cfg.full_screen and name in _CLOUD_PROVIDERS:
            logger.warning("full-screen capture is local-only; skipping cloud provider: %s", name)
            continue
        if os.getenv("AI_TRADER_SKIP_LOCAL", "") not in ("", "0") and name in _LOCAL_PROVIDERS:
            logger.info("AI_TRADER_SKIP_LOCAL set; skipping local provider: %s", name)
            continue
        names.append(name)
    if os.getenv("AI_TRADER_SKIP_LOCAL", "") not in ("", "0"):
        names = [name for name in names if name in _cloud_keys_present()]
    for cloud in reversed(_cloud_keys_present()):
        if cloud in names:
            names.remove(cloud)
            names.insert(0, cloud)
    names.append("noop")
    return names


def make_provider_chain(cfg: Config) -> list[Provider]:
    """Build provider backends in resolved order, always guaranteeing a ``noop``
    last resort so the chain never has zero backends (offline-safe).

    The effective order (see :func:`_order_chain`) is logged once at INFO so
    the intended chain is visible before any call happens — a keyed cloud
    shows first, a missing key shows the local-first fallback, and
    ``AI_TRADER_SKIP_LOCAL`` and full-screen are visible there too.
    """
    registry = _base_models()
    order = _order_chain(cfg)
    logger.info("provider chain resolved: %s", " -> ".join(order))
    built: list[Provider] = []
    for name in order:
        cls = registry.get(name)
        if cls is None:
            logger.warning("unknown provider in chain: %s", name)
            continue
        if name == "openrouter":
            built.append(cls(model=cfg.model_openrouter))
        elif name == "anthropic":
            built.append(cls(model=cfg.model_anthropic))
        elif name == "ollama":
            built.append(cls(model=cfg.model_ollama))
        elif name == "lmstudio":
            built.append(cls(model=cfg.model_lmstudio))
        else:
            built.append(cls())
    if not any(p.name == "noop" for p in built):
        built.append(NoOpProvider())
    return built


class VisionClient:
    """Resolves an analysis through the fallback chain behind the RateGuard.

    With rotation enabled (default), the effective provider order rotates each
    call so first-choice is spread across the chain (plan §3); ``noop`` is
    pinned last as the offline last resort.
    """

    def __init__(
        self,
        providers: list[Provider],
        guard: RateGuard,
        cooldown_seconds: float = 30.0,
        rotate: bool = True,
    ) -> None:
        self._by_name = {p.name: p for p in providers}
        self.guard = guard
        self.cooldown_seconds = cooldown_seconds
        self.rotate = rotate
        self._next_index = 0
        self._cooldown_until: dict[str, float] = {}

    def provider_names(self) -> list[str]:
        return list(self._by_name)

    def _ordered_chain(self) -> list[str]:
        """Effective provider order for the next call.

        Real providers rotate so first-choice is spread across the chain
        (plan §3: multi-provider rotation). A keyed cloud provider is pinned
        to first place on every call — the enablement decision says "use this
        provider"; the local fallback must never rotate ahead of it (it stays
        behind as the offline last resort). ``noop`` is pinned last with the
        same logic — never a real read, so never rotated into first position.
        """
        ordered = list(self._by_name)
        real = [name for name in ordered if name != "noop"]
        prime = next((cloud for cloud in _cloud_keys_present() if cloud in real), None)
        rest = [name for name in real if name != prime]
        if self.rotate and rest:
            start = self._next_index % len(rest)
            rest = rest[start:] + rest[:start]
            self._next_index += 1
        chain = ([prime] if prime else []) + rest
        if "noop" in ordered:
            chain.append("noop")
        return chain

    def _mark_cooldown(self, name: str) -> None:
        self._cooldown_until[name] = time.monotonic() + self.cooldown_seconds

    def analyze(
        self,
        image_b64: str,
        mime: str,
        prompt: str,
        chain: list[str] | None = None,
    ) -> ProviderResult:
        """Try each provider in ``chain`` (default: all registered, rotated so the
        first-choice is spread across real providers) and return the first
        successful analysis. Raises :class:`ProviderError` if none succeed."""
        chain = chain or self._ordered_chain()
        errors: list[str] = []
        for name in chain:
            provider = self._by_name.get(name)
            if provider is None:
                continue
            if self._cooldown_until.get(name, 0.0) > time.monotonic():
                errors.append(f"{name}: cooldown")
                continue
            if not self.guard.should_allow(name):
                errors.append(f"{name}: rate-limited")
                continue
            try:
                result = provider.analyze(image_b64, mime, prompt)
                logger.info("signal served by %s (%s)", name, result.model)
                return result
            except ProviderError as exc:
                self._mark_cooldown(name)
                errors.append(f"{name}: {exc}")
                logger.warning("provider %s failed, falling forward: %s", name, exc)
        raise ProviderError("all providers failed: " + "; ".join(errors))

    def close(self) -> None:
        for provider in self._by_name.values():
            close = getattr(provider, "close", None)
            if callable(close):
                close()
