# TradingView Order Ticket Mapping for AI Screener

This document describes how the screener should advise a person filling out the TradingView order ticket. The application remains advisory-only: it does not interact with the ticket, submit orders, or assume a user has entered/exited until they confirm it manually.

## Cross-market handling: Indian and US equities

AI Screener should support both Indian cash equities (NSE/BSE, INR) and US-listed equities (USD) based on a clearly visible exchange/ticker label. It must not assume all prices are INR or all share quantities are whole numbers.

- For NSE/BSE cash equities, quantity should be whole shares; the screener should report INR-based prices and use the INR-configured account balance.
- For US equities, show USD-based prices and use the USD-configured account balance. Fractional shares can be recorded only when the specific instrument and order ticket support them.
- If the chart market is unidentified, an exchange mismatch is detected, or the correct quote currency cannot be established, show WATCH/NO TRADE and do not present a fill-ready recommendation.
- Use NIFTY 50 and BANK NIFTY as context indices by default; they are not cash-equity shares. Do not apply stock share-quantity rules to their futures/options without a separate instrument-specific model.

## The three order modes in the supplied ticket screenshots

### Market
- Required order input: quantity (shares/units).
- Entry price is not guaranteed. Use the current bid/ask only as an estimate and explain possible slippage.
- Use only when a valid setup is confirmed and the person explicitly chooses immediate execution.
- The screener must still calculate/display a proposed stop-loss and target, and remind the user to verify that the exits are actually enabled/supported before submitting.

### Limit
- Required order inputs: limit price and quantity/cash value as supported by the ticket.
- BUY limit: the maximum price the user is willing to pay; normally set at or below the current tradable market price if trying to buy a pullback.
- SELL limit: the minimum price the user is willing to accept; normally set at or above the current tradable market price for a sell/exit or permitted short entry.
- The order can remain unfilled. The screener must show an expiry/invalidation rule and should not encourage leaving stale orders active after the setup has expired.

### Stop
- Required order inputs: trigger price and quantity/cash value as supported by the ticket.
- A BUY stop-entry is typically above the current market to trigger on a breakout; a SELL stop-entry is typically below the current market to trigger on a breakdown.
- TradingView describes its standard Stop order as a stop-market order: once triggered it becomes a market order and may fill beyond the trigger due to price movement/slippage.
- The supplied screenshot shows Take Profit and Stop Loss controls disabled for the Stop tab. TradingView documentation notes that Stop and Stop-limit orders currently do not support attaching TP/SL at order entry in that panel; the user may need to manage exits separately. Verify current behavior in the connected Paper Trading/account setup before relying on it.

## Shared ticket fields

### Quantity / currency value
The screener should show both the suggested whole-share quantity and the risk budget used to calculate it. The user must enter the actual size they intend to trade. If sizing inputs, share type, tick size, or instrument rules are unknown, show “quantity needs manual verification”.

Never assume a USD cash-value field and a shares field mean the same thing. The actual ticket can toggle between cash amount and shares. For NSE cash equities, use the share quantity and INR denomination; the screenshots supplied show TSLA/USD and are an example of the UI, not the target-market configuration.

### Take profit and stop loss
The displayed fields in the screenshots have toggles switched off. Merely seeing a target/stop price in the form is not proof the exit order is enabled or will be accepted.
- For supported order types, only treat TP/SL as configured after the user enables the toggle and checks the order preview/order list.
- For Stop orders where attached TP/SL is unavailable, explicitly state that exits may need to be added after entry.
- Never claim that a stop guarantees the exact execution price; a stop-market order can slip.

### Time in force and outside regular hours
- Time in force controls how long an unfilled pending order remains active. For intraday setups, start with Day where offered, rather than Week, unless the user deliberately wants a longer-lived order and the strategy has a matching expiry rule.
- The signal itself must expire when the setup is invalidated, stale, or the session ends. A signal's expiry and an order's time-in-force are separate; the user must cancel pending orders that outlive the signal.
- “Fill order outside RTH” and “Fill take profit outside RTH” relate to extended-hours trading for supported US instruments/features. Do not automatically enable them. For Indian cash equities, respect the instrument's permitted trading session and the user's actual order panel.

## How AI Screener should choose a mode (future strategy layer)

The model must not choose an order type based only on a screenshot or free-form reasoning. The deterministic strategy should classify the entry trigger:
- **Market candidate:** the entry condition is already confirmed and the user wants immediate entry, with acceptable spread/slippage.
- **Limit candidate:** the thesis depends on price returning to a planned pullback/retest level.
- **Stop candidate:** entry depends on price crossing a breakout/breakdown trigger. Show the trigger and warn about slippage.
- **NO TRADE:** data is stale, candles are incomplete, the price/market cannot be verified, risk/reward is below the configured filter, the screenshot and calculations disagree, or the user cannot set/verify risk exits.

For each actionable candidate, display: side, order type, trigger/limit/estimated market price, quantity, stop, target, planned R:R after estimated costs, signal expiry, market session, and rationale. Clearly mark any field that cannot be known reliably. Do not ask the user to place an order just because a VLM returns BUY/SELL.

## References

- TradingView, “How to place an order on TradingView”: https://www.tradingview.com/support/solutions/43000786671-how-to-place-an-order-on-tradingview/
- TradingView, “Types of orders: market, limit, stop”: https://www.tradingview.com/support/solutions/43000785102-types-of-orders-market-limit-stop/
- TradingView, “How to trade during extended hours”: https://www.tradingview.com/support/solutions/43000647250-how-to-trade-during-extended-hours/

Order ticket behavior can vary by instrument and connected broker/account. Verify the visible order preview and submitted order status for every manual order.
