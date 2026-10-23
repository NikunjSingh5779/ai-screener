# TradingView Free Indicator Setup (Indian + US Equity Intraday Research)

This is a starting configuration for experimentation on **NSE/BSE Indian equities and US-listed equities**, not a proven profitable strategy. All indicator names below are built-in TradingView indicators; no paid/community script is required.

## Shared versus market-specific setup

- Keep the exchange/ticker label visible in the screenshot. In the screener configuration, use `market = "AUTO"` to detect NSE, BSE or US from the displayed label; pin to one market while validating if the chart label is cropped or ambiguous.
- Use the chart's own exchange session and timezone. Indian cash-equity regular hours are typically 09:15–15:30 IST; US regular hours are typically 09:30–16:00 America/New_York. Pre-market and after-hours data must be distinguished from regular sessions.
- For the US, quote and journal values are in USD; for NSE/BSE cash equities they are in INR. Keep separate `account_size_usd` and `account_size_inr` settings for risk-based share sizing.
- Begin with stock shares only. NIFTY 50 and BANK NIFTY are indices, not cash shares; use them as context filters unless an instrument-specific derivatives strategy is later built.


## Chart layout

Use regular candlesticks (not Heikin Ashi) for price-level interpretation. Confirm that the ticker, exchange, timeframe and timezone/session are visible in the captured chart. Keep the chart region free of personal account details.

### 5-minute entry chart (both markets)

1. **VWAP** — built-in VWAP, Anchor Period = Session. Use as an intraday context line, not a standalone entry trigger.
2. **Volume** — built-in Volume. For initial research, compare completed-bar volume against the mean of the previous 20 completed bars; test whether this filter improves results instead of assuming it does.
3. **RSI** — built-in Relative Strength Index, Length = 14. Test >50 as bullish momentum context and <50 as bearish context. Do not treat 30/70 as automatic buy/sell instructions.
4. **ATR** — built-in Average True Range, Length = 14. Use only for volatility and stop-distance calculations; ATR does not indicate direction.
5. **EMA 20 and EMA 50** — add two built-in Exponential Moving Average indicators if you want short-term structure visible on the 5-minute chart.

### 15-minute confirmation (both markets)

Switch the chart timeframe to 15 minutes and add **EMA 20** and **EMA 50**. A candidate long setup can require EMA20 > EMA50 with price action agreeing; a candidate bearish setup can require EMA20 < EMA50. This is a testable filter, not a guarantee.

On a single 5-minute screenshot, the AI cannot reliably infer exact 15-minute EMA values from the underlying data for either market. For reliable automation, either provide a separate 15-minute screenshot/data input or add a small Pine Script companion that computes confirmed 15-minute values with `request.security()` and displays a clear trend-confirmation status on the 5-minute chart. Any higher-timeframe condition must use confirmed bars to avoid look-ahead/repainting problems.

## Candidate high-precision entry hypothesis to backtest

- Use the 15-minute EMA20/EMA50 relationship as the regime filter.
- Use the 5-minute close relative to session VWAP as one context filter.
- Test a breakout/pullback trigger using completed 5-minute candles and a recent swing level.
- Test relative volume (completed volume > previous 20-bar average) as a breakout quality filter.
- Use ATR and the relevant swing structure to propose a defensible stop.
- Reject the setup if the proposed target cannot offer at least 2R before fees/slippage, if data is stale, if an important chart value is ambiguous, or if visual and candle evidence disagree.
- Use a no-trade result when filters conflict. Avoid adding more indicators until out-of-sample results show measurable improvement.

## Evaluation requirements

Export the corresponding chart data where supported and compare it with screenshot timestamps. Use chronological training/validation/test periods and walk-forward evaluation. Include fees, slippage, signal latency, missing bars and realistic next-bar execution assumptions. Record precision, net expectancy in R, profit factor, drawdown, signal coverage, and false/stale signal rates.

A setup should remain research-only until it beats a simple baseline on unseen data and then passes an extended shadow/paper-trading period. This document is not a promise of profitability or investment advice.
