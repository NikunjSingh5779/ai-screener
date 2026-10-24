# AGENTS.md — ai screener (AI Trader)

Screen-reading, **advisory-only** desktop copilot for Indian (NSE/BSE) and US-listed equities. Captures a TradingView chart region, asks a local vision model for a structured read, and shows it in an always-on-top panel. It **never places, confirms, or auto-executes an order.**

## Hard constraints — do not relax

1. **Advisory only.** No order placement, no broker credentials, no automation of a fill. The overlay only *records* fills you confirm manually.
2. **$0 budget.** Local models only. Do not add a paid API as a default.
3. **Windows-first, but tests run cross-platform.** OS-specific calls (e.g. `winsound`) must degrade to a silent no-op elsewhere so `pytest` passes on Linux.
4. **Don't regress the Task 1–6 bug-fix pass:** Excel row targeting, `DEFAULTS` immutability, `client.close()` cleanup.
5. **Unclear chart ⇒ WATCH.** If the exchange label is unreadable or conflicts with the configured market, clear price levels and emit `WATCH`, never a direction.

## Layout

```text
ai_trader/
  cli.py            entry point: argparse, --once / --overlay / --watch, hotkeys
  config.py         DEFAULTS, TOML load, AI_TRADER_* env overrides
  capture.py        mss screen-region grab
  providers.py      provider chain (polystrata, ollama, lmstudio, openrouter, anthropic, noop)
  signal.py         prompt template, JSON parsing, TradingSignal model
  risk.py           compute_position_size() — advisory math only
  alerts.py         should_alert(), beep(), alert()
  overlay.py        Qt panel rendering
  overlay_ui.py     overlay worker loop / signal wiring
  order_ticket.py   TradingView order-ticket parsing
  logger.py         openpyxl writer for Trade_Log_Tracker.xlsx
  trade_tracker.py  manual fill / exit state
  rate_guard.py     min-interval + calls-per-minute budget
tests/              pytest suite (mirrors module names)
scripts/             validate_provider.py
```

## Commands

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item config.example.toml config.toml
python -m pytest -q                      # always run before claiming done

python -m ai_trader.cli --once            # single read, prints the signal
python -m ai_trader.cli --once --delay 3  # time to switch to the chart window
python -m ai_trader.cli --overlay --watch
python -m ai_trader.cli --region 100,50,900,600
```

Start with `--once` and verify signals manually before enabling `--watch`.

## Config (`config.toml`, from `config.example.toml`)

| Section | Key | Notes |
|---|---|---|
| `[capture]` | `region`, `monitor`, `full_screen` | Keep the overlay **outside** the captured region, or its text triggers false "changed frame" reads |
| `[providers]` | `chain`, `polystrata_base_url`, `[providers.budget]` | Default `["polystrata"]` → `http://127.0.0.1:8080/v1` |
| `[signal]` | `market` (`AUTO`\|`NSE`\|`BSE`\|`US`), `high_confidence_threshold` | `AUTO` reads the visible exchange label |
| `[risk]` | `account_size_inr`, `account_size_usd`, `risk_per_trade_pct` | Separate balances so INR and USD never mix; no FX guessing |
| `[logging]` | `hotkey` (default `f9`), `excel_path` | |

Every key also has an `AI_TRADER_*` env override (e.g. `AI_TRADER_PROVIDERS=ollama,noop` for a fully offline run). `noop`/`mock` exists **for tests only** — never act on it.

## Controls

| Input | Effect |
|---|---|
| No arguments | Open floating overlay, start monitoring |
| `F8` | Read the chart once |
| `F9` | Log the last displayed signal to the Excel tracker |
| Right-click overlay | Pause/resume, read once, log, quit |
| "I entered" / "Exit confirmed" | Record a manual fill/exit; no order is placed |

## Working rules

- **Run `pytest -q` after every change.** A passing test is the only proof.
- **One commit per task**, message names the task (e.g. `feat: per-market prompt tuning (Phase 5, Task 1)`).
- Read the existing test for a module before changing its behaviour; reuse its fixtures and the `QT_QPA_PLATFORM=offscreen` pattern from `tests/test_overlay.py`.
- Confidence is model-estimated, **not** calibrated probability. Directional signals are screened for ≥1.5:1 reward/risk; lower ratios become `WATCH`.
- Keep the smallest possible capture region — privacy and token cost both scale with pixels.

## Docs

| File | Contents |
|---|---|
| `README.md` | Full setup, local model notes, safety, controls |
| `ai-trader-project-plan.md` | Phased plan (source of scope) |
| `PHASE5_HANDOFF.md` | Phase 5 resume point — **written 2026-08-12, check git before trusting its "nothing is committed" claim** |
| `MODEL_AND_RUNTIME_CHECKLIST.md` | Pre-flight validation checklist |
| `TRADINGVIEW_SETUP.md`, `TRADINGVIEW_ORDER_TICKET.md` | Chart/order-ticket setup |

## AI-OS vault (optional, load on demand)

This project is linked to the knowledge vault at
`C:\Users\ASUS\OneDrive\Desktop\AI-OS obsidian`. Read a file only when the task
needs it — never preload the vault.

| Need | Path |
|---|---|
| User profile, stack, preferences | `wiki/entities/people/nikunj-singh.md` |
| Agent personas (33) | `agents/<name>.md` |
| Hermes/OmniRoute references (8) | `skills/<name>.md` |
| Prompt notes (19) | `prompts/<name>.md` |
| Research wiki (69 pages) | `wiki/home.md` |
| Project map | `projects/index.md` |

Sibling projects with related trading logic: `tradesense-platform`,
`ares-trading-system`, `trade-agent` (see the vault's `projects/index.md`).
