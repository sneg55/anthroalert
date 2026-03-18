# AnthroAlert – Automated Smart Money Trading Analysis System

**Powered Exclusively by Anthropic Claude Models + Official Nansen CLI**
**Version 1.2 | March 2026**

## 1. Product Overview

AnthroAlert is a fully automated AI agent system that delivers real-time **smart money alerts** for BTC perpetuals (Hyperliquid-focused) and extensible to other signals.

It uses the **official Nansen CLI** (https://github.com/nansen-ai/nansen-cli) as the sole data source. The CLI is purpose-built for AI agents, returns clean structured JSON, supports aggressive filtering (`--fields`, `--limit`, `--smart-money`), and is invoked via simple `subprocess` calls from Python.

**Performance Goal:** 8+ sub-agents every 30 minutes, zero manual intervention.

## 2. High-Level Architecture

**Brain (Design & Iteration Layer)**
- Tool: Claude.ai Pro subscription.
- Models: Claude Sonnet 4.6 / Opus 4.6 in chat.
- All prompt engineering, script generation, and CLI command tuning happens here.

**Body (Execution Layer)**
- Platform: Lightweight Python runner (Anthropic SDK + cron).
- Deploy: GitHub Actions, VPS, Replit, or local machine.
- Nansen CLI integration: `subprocess.run(["nansen", ...])` — zero extra Python dependencies.
- Model routing (single Anthropic key):
  - **Claude Haiku 4.5** → Fetch, Post, Coordinator, logging.
  - **Claude Sonnet 4.6** → Detect + Analysis.
- Prompt caching + batch API used automatically.

## 3. Sub-Agent Breakdown (Modular)

Each agent is isolated. Fetch only pulls what is needed.

### 1. Fetch Agent (Haiku 4.5 – every 30 min)
- Executes optimized Nansen CLI commands:
```bash
nansen research perp positions --symbol BTC --timeframe 30m \
  --fields wallet_address,position_size_usd,side,entry_price,leverage \
  --smart-money --limit 50 --sort position_size_usd:desc --pretty=false
```
- Or fallback: `nansen research smart-money netflow` / `perp-trades` with same filters.
- Runs `nansen schema perp positions --pretty` once at startup to validate fields/options.
- Saves raw JSON (or appends to SQLite).

### 2. Detect Agent (Sonnet 4.6 – only on fresh data)
- Reads saved JSON.
- Scores significance (notional size, wallet clustering, direction, deviation from baseline).
- Outputs: "Alert Worthy" + confidence + key metrics.
- Early exit if no signal.

### 3. Post Agent (Haiku 4.5 – only on signal)
- Formats clean alert message with Nansen-derived data (wallet links, notional, side, timestamp).
- Sends via Discord webhook to #nansen channel (1482110359375446300).
- NO Telegram - Discord only for now.

### 4. Coordinator Agent (Haiku 4.5 – every cycle)
- Orchestrates: Fetch → Detect → (Post if needed).
- Handles Nansen error codes (`CREDITS_EXHAUSTED`, `RATE_LIMITED`, `UNAUTHORIZED`).
- Logs summary.

### 5. Analysis Agent (Sonnet 4.6 – daily or on-demand)
- Aggregates multi-day history.
- Runs deeper queries (e.g. `nansen research smart-money netflow --timeframe 7d`).
- Generates digest or trend report.

## 4. Nansen CLI Integration Details

- **Installation (one-time):** `npm install -g nansen-cli` (Node.js 18+ required).
- **Auth:** `export NANSEN_API_KEY=...` (highest priority) or `nansen login`. Get key at https://app.nansen.ai/api.
- **Optimization levers built-in:**
  - `--fields` (reduces response payload)
  - `--limit 20–50`
  - `--timeframe 30m/1h`
  - `--smart-money` flag + `--labels`
  - `--stream` if ever needed for larger pulls
- **Error handling:** Auto-retry on `RATE_LIMITED`; stop on `CREDITS_EXHAUSTED`.
- **Perps scope:** Hyperliquid-only (native `--symbol BTC` support).
- **Extensibility:** Add any `nansen research` category (token, portfolio, points, etc.) in minutes.

## 5. Core Features

- Real-time Telegram alerts with direct wallet links and Nansen-sourced metrics.
- Native Hyperliquid BTC perps smart-money coverage out of the box.
- Aggressive data filtering via `--fields`, `--limit`, and `--smart-money` flags.
- Modular sub-agents with early-exit logic.
- Thresholds and filters fully editable.
- Historical dataset retention for multi-day trend analysis.
- Full audit logs of every CLI call and Claude decision.
- Optional Streamlit dashboard for visual summary.
- Seamless extensibility for funding-rate divergence, OI spikes, cross-chain signals.

## 6. Technical Requirements

- **Prerequisites:** Node.js 18+, npm, NANSEN_API_KEY, Telegram Bot token.
- **Language:** Python 3.11+ (only `anthropic`, `subprocess`, `json`, `sqlite3`).
- **Dependencies:** None beyond Anthropic SDK (Nansen is external CLI).
- **Scheduler:** cron / GitHub Actions / APScheduler.
- **Security:** Keys in `.env`; all data stays local.

## 7. Environment Variables Needed

```bash
NANSEN_API_KEY=GnxtDvfz9x5zRkv4NFQJdAeaxCunBtCC
ANTHROPIC_API_KEY=<your key>
DISCORD_WEBHOOK_URL=<webhook for #nansen channel>
# OR use openclaw CLI: openclaw system event --text "alert message" --channel 1482110359375446300
```

## 8. File Structure

```
anthroalert/
├── agents/
│   ├── __init__.py
│   ├── fetch.py        # Fetch Agent
│   ├── detect.py       # Detect Agent
│   ├── post.py         # Post Agent
│   ├── coordinator.py  # Coordinator Agent
│   └── analysis.py     # Analysis Agent
├── data/
│   └── positions.db    # SQLite for historical data
├── logs/
│   └── .gitkeep
├── main.py             # Entry point (runs coordinator)
├── config.py           # Configuration & thresholds
├── requirements.txt
├── .env.example
└── README.md
```
