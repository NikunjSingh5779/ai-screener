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

## Local model — PolyStrata / Strata (recommended for your setup)

You use [PolyStrata](https://github.com/VecSzn/PolyStrata) and [Strata](https://github.com/Niko1221/Strata). Both expose an OpenAI-compatible local API at `http://127.0.0.1:8080/v1` by default. PolyStrata offers image-capable Qwen3.6 and Tiel models; Strata serves Qwen3.8-Flash-Next with image input when enabled. The screener's `polystrata` provider sends the captured chart as an `image_url` message to that local endpoint.

Start ONE of the runtimes and load an image-capable model. In the runtime UI or terminal, verify the model exposes image input. Then check the local endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health
Invoke-RestMethod http://127.0.0.1:8080/v1/models
```

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
