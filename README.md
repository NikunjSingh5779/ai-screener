# AI Trader — Floating Real-Time Trading Copilot (Phase 0/2)

An always-on-top, screen-watching trading copilot (per `ai-trader-project-plan.md`):
capture a screen region → send it to a vision LLM → get a structured trading call
(action, entry, stop-loss, target, position size, confidence). Phase 1 prints it to
the console; Phase 2 renders it in a floating always-on-top panel over your platform.

**This is advisory software. It never places an order.** The tool only tells you what
it thinks — a hallucinated signal can never cost you money by itself.

Current build: **Phase 0–4** (provider validation, CLI signal reader, floating overlay, 
continuous polling with fallback rotation, and Excel trade logging). 
Phases 5–6 (refinement, local fallback) are future work.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 1. Configure your screen region
Copy-Item config.example.toml config.toml   # then edit the region to your chart window

# 2. Add a provider key (any one — the chain uses whichever is present)
$env:OPENROUTER_API_KEY = "sk-or-..."     # free tier (recommended, $0)
#   or
$env:ANTHROPIC_API_KEY  = "sk-ant-..."

# 3. Verify a provider can read your chart (Phase 0)
python scripts\validate_provider.py

# 4. Run the reader (Phase 1) — press F8 to read the region
python -m ai_trader.cli
```

If the global hotkey needs admin rights on your Windows setup, the CLI automatically
falls back to an interactive "press Enter to read" loop.

### Phase 2 — floating overlay

```powershell
# Same keys as Phase 1. Run the always-on-top panel (F8 reads; F9 logs trade; right-click for menu):
python -m ai_trader.cli --overlay

# Run the panel in watch mode (polls continuously, skips unchanged frames):
python -m ai_trader.cli --overlay --watch
```

The panel floats above your trading platform and never steals keyboard focus. It shows
the latest call — action, confidence, entry/stop/target/size, reasoning — plus the
signal's age ("signal is 8s old"), greying to red when a call goes stale (>5 min).
Requires `PyQt6` (installed by `pip install -r requirements.txt`).

## Without any API key

The `noop` provider (offline mock, $0) makes the entire pipeline run and is fully
testable. Every provider attempt and fallback is logged, and the CLI prints which
provider/model served each signal so quality changes stay traceable.

## Providers

| Provider | Key | Cost | Notes |
|---|---|---|---|
| `ollama` | — | $0 | Local; attempted **first** by default (needs `ollama serve` + a vision model, e.g. `ollama pull moondream`) |
| `noop` | — | $0 | Deterministic mock; default when no key is set |
| `openrouter` | `OPENROUTER_API_KEY` | $0 | Free vision models; `max_price=0` hard bound so a paid model can never be charged |
| `anthropic` | `ANTHROPIC_API_KEY` | paid | Via Messages API (vision image blocks) |

The first provider in the chain is `ollama` by default (run `ollama serve` locally and
`ollama pull moondream` for a free, offline vision model); the chain falls back to the
API providers in order when one is missing or fails.

Models are editable in `config.toml` → `[providers.models]`.

## Tests

```powershell
pytest
```

All tests run offline (mock provider, no API calls). The two PyQt6 panel smoke tests
auto-skip when PyQt6 is not installed.
