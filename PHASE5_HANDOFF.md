# Phase 5 Handoff — resume point (2026-08-12)

**Task:** Implement Phase 5 ("Refinement") from `ai-trader-project-plan.md`:
1. Per-market prompt tuning
2. Real risk-% position sizing
3. High-confidence alerts
(+ optional bonus: Phase 6 provider-outage fallback test)

**Constraints (do not relax):** Advisory only (never place/confirm/auto-execute a
trade). $0 budget. Windows-first — OS-specific code (e.g. `winsound`) degrades to
a silent no-op on Linux so tests pass. Do not regress the Task 1–6 bug-fix pass
(Excel row-targeting, `DEFAULTS` immutability, `client.close()` cleanup).
Run `pytest -q` after each task; one commit per task (message references the task).

## State on resume

- **NOTHING is committed.** Working tree on `main` has uncommitted changes.
- The **tool-result channel was flooded** by concurrent background agents: file
  reads and command output (pytest) did not come back, so **none of the edits
  below have been verified** by a passing test. **Verify `pytest -q` first.**

## Already applied (unverified)

- `ai_trader/config.py`
  - `DEFAULTS["signal"]["market_notes"]` — NSE/BSE/US/Crypto (currency, session
    hours, F&O caution; Crypto = "24/7 — ignore session-hours").
  - `DEFAULTS["signal"]["high_confidence_threshold"] = 80` (Task 3).
  - `DEFAULTS["risk"] = {"account_size": 0.0, "risk_per_trade_pct": 1.0}` (Task 2).
  - `_ENV_OVERRIDES` += `AI_TRADER_HIGH_CONFIDENCE`, `AI_TRADER_ACCOUNT_SIZE`,
    `AI_TRADER_RISK_PCT` (float-coerced in `_apply_env_overrides`).
  - `Config` += `market_notes: dict[str,str]`, `high_confidence_threshold: float`,
    `account_size: float`, `risk_per_trade_pct: float`; populated in `load_config`
    via `.get(...)` with safe defaults.
- `ai_trader/signal.py`
  - `DEFAULT_PROMPT_TEMPLATE` += `Market notes: {market_notes}`.
  - `SignalEngine.__init__` += `market_notes: dict[str,str] | None = None`.
  - `build_prompt()` fills `{market_notes}` from `self.market_notes.get(self.market, "")`.
  - `TradingSignal.quantity: float | None = None` (reuses logger's existing
    `getattr(signal, "quantity", None)` for Excel column K).
- `ai_trader/cli.py`
  - Imports `alert` and `compute_position_size`.
  - `build_pipeline` passes `market_notes=cfg.market_notes` to `SignalEngine`.
  - New `_emit_signal(cfg, result)`: computes+sets `signal.quantity`, prints both
    sizes (model `position_size_pct` guess vs risk-sized qty, or the "set
    `risk.account_size` in config.toml" note), then `alert(signal, threshold)` —
    called only for signals past `guard_flip`. Used by `do_read` and `run_cli_watch`.
- `ai_trader/risk.py` (new): `compute_position_size(account_size,
  risk_per_trade_pct, entry, stop_loss) -> float | None`. Formula:
  `risk_amount = account_size*risk_pct/100`; `per_unit_risk = abs(entry-stop)`;
  `None` if entry/stop missing, equal, or `account_size <= 0`.
- `ai_trader/alerts.py` (new): `should_alert(signal, threshold)` (buy/sell
  at/above threshold only), `beep()` (winsound, silent no-op elsewhere),
  `alert(signal, threshold, beep_fn=beep)` (injectable mock).

## Pending (do these next, in order)

1. **Verify:** `python -m pytest -q` — must be green (72 existing tests). If the
   result channel is still flooded, restart the session and re-run.
2. **Task 2 overlay wiring** (`overlay_ui.py`): in the worker completion path
   (`_read_worker` / `_on_signal`), after a parsed signal that passes
   `guard_flip`, set `signal.quantity = compute_position_size(cfg.account_size,
   cfg.risk_per_trade_pct, entry, stop_loss)` and display both sizes in the panel
   (or the "position sizing unavailable" note).
3. **Task 3 overlay wiring + visual state** (`overlay_ui.py`, `overlay.py`):
   call `alert(...)` for signals shown (post-`guard_flip`); give the panel a
   distinct high-confidence state (colored border/badge). Reuse the offscreen-Qt
   pattern (`QT_QPA_PLATFORM=offscreen`) from `tests/test_overlay.py`.
4. **Acceptance tests** (match existing fixtures/patterns — read the current
   tests first):
   - `tests/test_risk.py`: normal case (e.g. account 100000, risk 1%, entry 100,
     stop 99 → 1000.0); entry==stop → None; either level None → None;
     account_size<=0 → None.
   - `tests/test_signal.py`: prompts for two markets each contain their own note
     and not the other's; unknown market renders cleanly (no `{market_notes}`).
   - `tests/test_logger.py`: reuse the real-tracker-copy fixture; log a signal
     with `quantity` set → lands in column K (Quantity) of the correct row; the
     pre-existing Capital-Risked formula in column M stays intact (or evaluates).
   - `tests/test_alerts.py`: `should_alert` True only buy/sell at/above threshold;
     False hold/watch regardless; False below threshold; `alert()` calls injected
     `beep_fn` exactly once for qualifying, zero otherwise.
   - `test_cli.py` / `test_overlay.py`: high-confidence directional past
     `guard_flip` triggers alert; suppressed by `guard_flip` does not.
   - Task 2 accept: when `risk.account_size` unset/zero, `quantity` stays None and
     CLI/overlay display path doesn't crash.
5. **Bonus (Phase 6):** test that when every cloud provider raises (mock
   openrouter/anthropic), the chain still signals via `ollama` or falls through to
   `noop` without crashing. Mark Phase 6 done in the plan.
6. **Commits:** one per task, e.g. `feat: per-market prompt tuning (Phase 5, Task 1)`.

## Resume prompt for a new Claude Code session

> Working dir: `C:\Users\ASUS\OneDrive\Desktop\ai screener` (branch `main`).
> Read `PHASE5_HANDOFF.md` and `ai-trader-project-plan.md`, then continue Phase 5.
> The Task 1–3 config/logic edits are already applied but unverified. Run
> `python -m pytest -q` first and get it green, then finish the overlay wiring
> and acceptance tests exactly as the handoff lists, one commit per task.
