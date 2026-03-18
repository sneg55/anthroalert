"""AnthroAlert configuration — thresholds, models, and CLI settings."""

import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "positions.db"
LOG_DIR = BASE_DIR / "logs"

# ── API Keys (loaded from .env) ───────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
NANSEN_API_KEY = os.getenv("NANSEN_API_KEY", "")

# ── Model Routing ─────────────────────────────────────────────────────
MODEL_HAIKU = "claude-3-5-haiku-20241022"      # Fetch, Post, Coordinator
MODEL_SONNET = "claude-sonnet-4-20250514"      # Detect, Analysis

# ── Alert Delivery ────────────────────────────────────────────────────
# Telegram (primary)
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Discord (backup)
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# ── Nansen CLI ────────────────────────────────────────────────────────
NANSEN_CMD = "nansen"

# Primary data commands
NANSEN_SMART_MONEY_PERP_TRADES = [
    NANSEN_CMD, "research", "smart-money", "perp-trades",
    "--pretty=false",
]

NANSEN_TOKEN_PERP_POSITIONS = [
    NANSEN_CMD, "research", "token", "perp-positions",
    "--symbol", "BTC",
    "--pretty=false",
]

NANSEN_TOKEN_PERP_TRADES = [
    NANSEN_CMD, "research", "token", "perp-trades",
    "--symbol", "BTC",
    "--days", "1",
    "--pretty=false",
]

NANSEN_SMART_MONEY_NETFLOW = [
    NANSEN_CMD, "research", "smart-money", "netflow",
    "--chain", "ethereum",
    "--pretty=false",
]

# ── Detection Thresholds ──────────────────────────────────────────────
# Minimum notional (USD) for a single position/trade to be alert-worthy
MIN_NOTIONAL_USD = 500_000

# Minimum number of smart-money wallets moving in the same direction
MIN_CLUSTER_SIZE = 3

# Confidence floor — Detect Agent must score above this to trigger alert
MIN_CONFIDENCE = 0.65

# Maximum age (seconds) of data before it's considered stale
MAX_DATA_AGE_SECONDS = 1800  # 30 minutes

# ── Scheduling ────────────────────────────────────────────────────────
CYCLE_INTERVAL_SECONDS = 1800  # 30 minutes between cycles

# Analysis agent schedule (daily)
ANALYSIS_INTERVAL_SECONDS = 86400

# ── Retry / Error Handling ────────────────────────────────────────────
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # exponential: 2, 4, 8 seconds
RATE_LIMIT_WAIT_SECONDS = 60

# Nansen error codes that trigger specific behaviour
NANSEN_RETRYABLE_ERRORS = {"RATE_LIMITED"}
NANSEN_FATAL_ERRORS = {"CREDITS_EXHAUSTED", "UNAUTHORIZED"}

# ── Anthropic SDK Settings ────────────────────────────────────────────
ANTHROPIC_MAX_TOKENS = 1024
ANTHROPIC_TEMPERATURE = 0.2
