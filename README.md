# AI Trader — Local-First Floating Trading Copilot

AI Trader is a **screen-reading, advisory-only** desktop copilot. It captures a selected chart region, asks a vision model for a structured interpretation, displays the result in an always-on-top panel, and can log reviewed signals to an Excel workbook. It **does not place orders**.

## Quick start (Windows PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item config.example.toml config.toml
python -m pytest -q
```

Set the screen region in `config.toml` to the chart's pixel coordinates. Start with **manual reads**, not continuous polling, until you have verified the capture region and model output.

## Local model — no API key

The app already supports a local Ollama vision endpoint and a local OpenAI-compatible endpoint (LM Studio/llama.cpp). Ollama is at `http://localhost:11434`; LM Studio defaults to `http://localhost:1234/v1`.

### Ollama (lowest-friction option)

Install Ollama, then test the app with a supported image model, for example:

```powershell
ollama pull qwen2.5-vl:7b
ollama list
```

In `config.toml`, retain `ollama = "qwen2.5-vl:7b"` and set the provider chain to local-only for predictable testing:

```toml
[providers]
chain = ["ollama", "noop"]
```

Then run:

```powershell
python -m ai_trader.cli --once
python -m ai_trader.cli --overlay
# Only after confirming the region + readings:
python -m ai_trader.cli --overlay --watch
```

The `noop` provider is a deterministic test mock, **not an actual market analysis**. If it served the result, do not treat the output as a trading signal. The startup log prints the effective provider chain and each successful response's provider/model.

### Larger local vision models

Qwen3.6-35B-A3B is multimodal, but common 4-bit GGUF builds are around 20–23 GB before runtime context/cache, so they cannot reside fully in 12 GB VRAM. Partial GPU offload to system RAM is possible with a compatible engine, but latency can rise substantially. Verify actual model format, vision projector, engine support, RAM use, and end-to-end latency before making it the default. Do not confuse 3B active parameters with a 3 GB model.

The ISTA-DASLab Qwen3.8-Flash-Next GSQ-RCO Coder IQ1_M artifact is an experimental code-specialized, expert-pruned multimodal model. Its first shard alone is about 29.6 GB and must reside in memory; the second 28.8 GB n-gram table can be memory-mapped from SSD. It is not a good default for this screenshot-reading app on a 12 GB GPU, despite the low-bit name. Use it for offline coding experiments only if your host RAM/SSD and serving engine can handle it, not as the first runtime to debug.

## Local OpenAI-compatible endpoint

For a vision-capable GGUF in llama.cpp, confirm the matching multimodal projector (`mmproj`) and use your engine's OpenAI-compatible server. Configure:

```powershell
$env:AI_TRADER_LMSTUDIO_BASE_URL = "http://127.0.0.1:1234/v1"
$env:AI_TRADER_LMSTUDIO_MODEL = "the-model-id-returned-by-v1-models"
$env:AI_TRADER_PROVIDERS = "lmstudio,noop"
python -m ai_trader.cli --once
```

For the app to submit images, the loaded model **must be vision-enabled** and its serving engine must support OpenAI-compatible `image_url` content. Check the endpoint before starting the app:

```powershell
Invoke-RestMethod http://127.0.0.1:1234/v1/models
```

A text-only coder does not become a vision model simply because the client sends an image.

## Safety, privacy, and testing

- Default provider chain can include cloud APIs. For offline/local testing set `AI_TRADER_PROVIDERS=ollama,noop` or `AI_TRADER_PROVIDERS=lmstudio,noop`. Do not configure cloud providers if screenshots must stay local.
- Full-screen capture is local-only; prefer the smallest possible chart region to reduce privacy exposure and vision token/latency costs.
- Start with `--once`; use `--overlay --watch` only after validating signals manually. Configure a realistic refresh interval: every image analysis may be slow and unchanged frames are skipped.
- Confidence is model-estimated, not calibrated probability. Never treat AI output as an instruction to enter a trade.
- Position sizing is advisory math only. It assumes the provided entry/stop are correct, does not account for slippage/fees/lot sizes, and never places an order.
- `noop/mock` exists for software tests only. Never act on mock output.
- Run the offline test suite before use: `python -m pytest -q`.

## Key controls

- `F8`: read chart once
- `F9`: log the last displayed signal to the configured Excel tracker
- Right-click overlay: read, log, or quit
- `--once`: perform a single read and exit
- `--watch`: CLI polling mode
- `--overlay --watch`: floating panel + polling

Keep credentials in `.env`; never commit secrets.
