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

With no command-line arguments, the app opens the floating overlay and starts monitoring automatically. Right-click the overlay to pause/resume monitoring, read once, log, or quit. Set `[capture].region` in `config.toml` to the chart's pixel coordinates and keep the overlay outside that region; otherwise, the overlay's changing text can make the captured image appear changed. Start with a small chart-only region and verify it before leaving monitoring enabled. The displayed polling interval is automatically raised when needed to respect both the local provider's minimum request gap and its configured calls-per-minute budget; changing chart pixels can still trigger a new analysis at each eligible poll.

## Indian + US equity support

The default market selection is `AUTO`. The model is prompted to identify the visible exchange label and the screener normalizes supported labels to `NSE`, `BSE`, or `US`. Set `[signal].market` to `NSE`, `BSE`, or `US` to pin the app to one market while testing. Keep the ticker and exchange visible in your capture region. If the configured market conflicts with the model's result—or the exchange cannot be identified—the app clears trade price levels and emits `WATCH`, not a directional signal.

- **Indian equities:** quote currency INR; configure `risk.account_size_inr` to enable account-risk-based share sizing.
- **US equities:** quote currency USD; configure `risk.account_size_usd` to enable US share sizing. Fractional quantity can be recorded manually for US trades where the product supports it.
- The old `risk.account_size` key remains for backward compatibility and is used only in the currency declared by `risk.account_currency` (default INR). No FX conversion is guessed.
- The supported market sessions and symbols must be clear from the chart. This is screenshot-based market identification, not a live market-data feed or a guarantee that the screenshot is fresh.

## Local model — PolyStrata / Strata (recommended for your setup)

You use [PolyStrata](https://github.com/VecSzn/PolyStrata) and [Strata](https://github.com/Niko1221/Strata). Both expose an OpenAI-compatible local API at `http://127.0.0.1:8080/v1` by default. PolyStrata offers image-capable Qwen3.6 and Tiel models; Strata serves Qwen3.8-Flash-Next with image input when enabled. The screener's `polystrata` provider sends the captured chart as an `image_url` message to that local endpoint.

Start ONE of the runtimes and load an image-capable model. In the runtime UI or terminal, verify the model exposes image input. Then check the local endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health
Invoke-RestMethod http://127.0.0.1:8080/v1/models
```

The local request now uses a more detailed chart-reading checklist, `reasoning_effort = "low"`, `reasoning_budget_tokens = 1024`, and `max_tokens = 8192` for the response. This bounds internal reasoning so it does not consume the full completion before the JSON signal is emitted. The output-token cap is not the prompt-reading cap: Strata's “reading the prompt” count shows the actual input it processed, and the expanded instructions should make that number higher than the previous ~747-token prompt. The exact count depends on the tokenizer and selected market notes. The earlier 4,096-token trial was exhausted by internal thinking before an answer was emitted; increasing the output cap alone was not enough. The app now sends a low reasoning effort and a 1,024-token reasoning budget, while reserving an 8,192-token maximum completion allowance. This does not force the model to use the full allowance or guarantee better trading decisions.

### Increase image detail for chart reading (Strata setting)

On the official Strata runtime, the CPU image reader used on GPUs below 24 GB can default to a maximum of 300 image tokens. That is separate from the text prompt's token count. For chart labels, small price-axis text and indicator names, raise the active model config's `vision.max_tokens` to `1024`. Strata documents this as a higher-detail option; image encoding takes longer, especially on CPU. If the current config already has a `vision` object, preserve its other keys and change only `max_tokens` rather than replacing the object. See the [Strata image-token documentation](https://github.com/Niko1221/Strata/blob/main/docs/DETAILS.md#images).

Example fragment to merge into the active `strata-<model>.json` file (do not overwrite unrelated settings):
```json
"vision": {
  "max_tokens": 1024
}
```

For the current 12 GB GPU setup, keep the chart capture region tight and test latency before leaving continuous monitoring running. Do not raise the context window just to fix chart reading: context is a separate total-token budget, and a ~1K text prompt is far below even a modest context window. The image-token limit controls visual detail; the prompt checklist controls what the model is asked to look for.

The model's context window is a separate Strata runtime setting. Inspect what the loaded server actually reports rather than blindly setting it higher:
```powershell
Invoke-RestMethod http://127.0.0.1:8080/props | ConvertTo-Json -Depth 8
Invoke-RestMethod http://127.0.0.1:8080/v1/models | ConvertTo-Json -Depth 8
```
Use the reported context limit and image-input capability as the source of truth. The local app's request cap cannot enlarge the context window compiled/configured in the server.

Copy `config.example.toml` to `config.toml`; the example sets local-only `chain = ["polystrata", "noop"]`, base URL `http://127.0.0.1:8080/v1`, and model ID `strata`. If `/v1/models` reports another model ID, set `[providers.models].polystrata` to that exact ID. For example:

```toml
[providers]
chain = ["polystrata", "noop"]
polystrata_base_url = "http://127.0.0.1:8080/v1"
polystrata_api_key = "" # local default; set if you configured API auth

[providers.models]
polystrata = "strata" # replace with the ID returned by /v1/models
```

Then run a single read first:

```powershell
python -m ai_trader.cli --once
python -m ai_trader.cli --overlay
# Only after confirming the region and real model response:
python -m ai_trader.cli --overlay --watch
```

If you configure an API key on PolyStrata/Strata, set `polystrata_api_key` to that key. Keep the server bound to `127.0.0.1`; do not expose it to your LAN for this local desktop workflow. The exact API model ID is runtime-specific, so do not assume that every installed model reports the default name.

### Choosing between your local models

- **First baseline:** an image-enabled Qwen3.6-35B-A3B or other vision-capable PolyStrata model. It is designed to read images; on 12 GB VRAM, expect some weight/vision processing to use system RAM and benchmark the full request latency.
- **Second comparison:** Qwen3.8-Flash-Next through Strata, with image input enabled. Compare chart identification, price-level abstention and end-to-end latency against Qwen3.6.
- **Tiel Coder 35B-A3B:** PolyStrata documents it as image-capable, but its coder specialization makes it a comparison candidate rather than my first chart-reading choice.
- Run one local model server at a time on port 8080 unless you have deliberately changed the port. Switch models in the runtime, check `/v1/models`, then rerun the screener.

Do not use a model's coding benchmark as evidence of chart-reading accuracy. Compare both models on the same set of timestamped TradingView screenshots, with human-verified ticker/timeframe/price levels and measured latency. If the response is from `noop/mock`, the panel must show TEST MODE and the output is not a market signal.

### Alternate local backends

The project still supports Ollama and LM Studio / generic OpenAI-compatible endpoints. Set `AI_TRADER_PROVIDERS` to choose the chain explicitly; do not leave unused cloud backends in the chain if your requirement is no cloud requests.

- Ollama default: `http://127.0.0.1:11434`.
- LM Studio default: `http://127.0.0.1:1234/v1`.
- PolyStrata / Strata default: `http://127.0.0.1:8080/v1`.

## Safety, privacy, and testing

- Default provider chain can include cloud APIs. For offline/local testing set `AI_TRADER_PROVIDERS=ollama,noop` or `AI_TRADER_PROVIDERS=lmstudio,noop`. Do not configure cloud providers if screenshots must stay local.
- Full-screen capture is local-only; prefer the smallest possible chart region to reduce privacy exposure and vision token/latency costs.
- Start with `--once`; use `--overlay --watch` only after validating signals manually. Configure a realistic refresh interval: every image analysis may be slow and unchanged frames are skipped.
- Confidence is model-estimated, not calibrated probability. Never treat AI output as an instruction to enter a trade.
- Directional signals with visible entry/stop/target are screened for a minimum 1.5:1 reward/risk ratio; lower-ratio suggestions become WATCH with actionable levels cleared. This is a screening heuristic, not evidence of profitability.
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

- Start with no arguments: open the floating overlay and start chart monitoring
- Right-click overlay: pause/resume monitoring, read once, log, or quit
- `F8`: read chart once
- `F9`: log the last displayed signal to the configured Excel tracker
- **I entered**: record actual manual fill; no order placed
- **Exit confirmed**: record actual manual exit; closes the tracker entry
- Right-click overlay: read, log to the Excel signal tracker, or quit
- `--once`: perform a single read and exit
- `--watch`: CLI polling mode
- `--overlay --watch`: floating panel + polling

Keep credentials in `.env`; never commit secrets.


### Deterministic safety guards

Directional suggestions are checked in application code after model parsing and before
the overlay, risk sizing, or logging. Checks cover supported markets, common
cryptocurrency pairs, configured market/symbol conflicts, missing/non-finite levels,
incorrect BUY/SELL ordering, and reward/risk below 1.5:1. Failed checks downgrade
the suggestion to WATCH and clear actionable levels. Model/Mica classifications are
advisory only and cannot override these checks.

The screenshot-only provider interface does not expose the latest market candle's
timestamp. Capture time is not market-data freshness evidence. Therefore, BUY/SELL
suggestions are downgraded to WATCH until a trusted market-data adapter supplies
timestamp-derived freshness evidence. HOLD/WATCH responses remain available. Do not
bypass this guard just to obtain directional suggestions; connect a reliable timestamp
source and validate its latency and clock semantics first.
