# AI Trader — Local-First Floating Trading Copilot

AI Trader is a **screen-reading, advisory-only** desktop copilot for **Indian (NSE/BSE) and US-listed equities**. It captures a selected TradingView chart region, asks a local vision model for a structured interpretation, normalizes the detected market and quote currency, displays the result in an always-on-top panel, and can track manually confirmed fills. It **does not place orders**. Market auto-detection depends on a readable ticker/exchange label; unclear or unsupported markets are downgraded to WATCH.

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

## Indian + US equity support

The default market selection is `AUTO`. The model is prompted to identify the visible exchange label and the screener normalizes supported labels to `NSE`, `BSE`, or `US`. Set `[signal].market` to `NSE`, `BSE`, or `US` to pin the app to one market while testing. Keep the ticker and exchange visible in your capture region. If the configured market conflicts with the model's result—or the exchange cannot be identified—the app clears trade price levels and emits `WATCH`, not a directional signal.

- **Indian equities:** quote currency INR; configure `risk.account_size_inr` to enable account-risk-based share sizing.
- **US equities:** quote currency USD; configure `risk.account_size_usd` to enable US share sizing. Fractional quantity can be recorded manually for US trades where the product supports it.
- The old `risk.account_size` key remains for backward compatibility and is used only in the currency declared by `risk.account_currency` (default INR). No FX conversion is guessed.
- The supported market sessions and symbols must be clear from the chart. This is screenshot-based market identification, not a live market-data feed or a guarantee that the screenshot is fresh.

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

## TradingView chart and order-ticket guide

- [Free intraday indicator setup](TRADINGVIEW_SETUP.md): built-in EMA 20/50, Session VWAP, Volume, RSI 14 and ATR 14; use as backtest hypotheses, not proven trading rules.
- [Market / Limit / Stop order-ticket mapping](TRADINGVIEW_ORDER_TICKET.md): tells the screener design which fields differ by order type, how stops/targets and time-in-force should be treated, and why the app must not assume orders were submitted.

## Manual trade tracking in the overlay

The floating panel provides **I entered** and **Exit confirmed** controls. They only record fills that you already executed yourself; they do not place orders.

1. Read a real BUY/SELL signal with entry, stop and target levels.
2. After your own fill, click **I entered** and enter the actual fill price and whole-share quantity.
3. When you personally close the position, click **Exit confirmed** and enter the actual exit fill price.
4. The app appends the completed trade to `Manual_Trade_Journal.csv`, including holding time, gross P&L before fees/slippage and realized R multiple where the stop-loss allows calculation.
5. `Manual_Trade_State.json` preserves the active tracked position if the overlay is restarted.

These files are local user data and are gitignored. Back them up if you need to retain the journal. A manually tracked position is not synchronized with a broker or TradingView. For an NSE cash-equity SELL signal, the panel asks for an explicit confirmation before tracking it as a short; only continue if your selected product/account supports that trade. If you are selling shares you already own, do not treat that as a new short entry.

## Key controls

- `F8`: read chart once
- `F9`: log the last displayed signal to the configured Excel tracker
- **I entered**: record actual manual fill; no order placed
- **Exit confirmed**: record actual manual exit; closes the tracker entry
- Right-click overlay: read, log to the Excel signal tracker, or quit
- `--once`: perform a single read and exit
- `--watch`: CLI polling mode
- `--overlay --watch`: floating panel + polling

Keep credentials in `.env`; never commit secrets.
