# Runtime validation checklist

## Before first use
- [ ] `python -m pytest -q` passes.
- [ ] `ollama list` shows the configured image-capable model.
- [ ] Set `AI_TRADER_PROVIDERS=ollama,noop` for local-only inference.
- [ ] Capture coordinates show only the chart, not account/personal data.
- [ ] `python -m ai_trader.cli --once` reports the actual provider/model; reject `noop/mock`.
- [ ] Verify symbol, timeframe and levels against the chart; prefer HOLD if any are ambiguous.
- [ ] Confirm stale/unchanged-frame handling, overlay rendering and Excel logging.
- [ ] Never enable order execution; this application is advisory-only.

## Model comparison
Build a fixed set of chart screenshots with personal/account info removed. Log image ID, model, latency, parse failures, symbol correctness, price-level correctness and whether ambiguous reads correctly return HOLD/WATCH. Compare against human labels; model confidence scores alone do not establish accuracy.
