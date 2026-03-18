# AnthroAlert

Automated smart money alert system powered by [Nansen CLI](https://github.com/nansen-ai/nansen-cli).

Real-time Hyperliquid perp alerts when whales move.

## Features

- **Smart Money Alerts** — Detect large trades from labeled wallets (Galaxy Digital, Funds, Smart Traders)
- **Telegram Notifications** — Formatted alerts with ticker hashtags, fill prices, and wallet addresses
- **Trailing Stop Bot** — Automated stop-loss management for Hyperliquid positions

## Quick Start

### Prerequisites

```bash
# Install Nansen CLI
npm install -g nansen-cli

# Install Hyperliquid CLI (for trailing stops)
npm install -g hyperliquid-cli
```

### Setup

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/anthroalert.git
cd anthroalert

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your API keys
```

### Run Smart Money Alerts

```bash
# Single run
python simple_alert.py

# Or schedule with cron (every 30 min)
*/30 * * * * cd /path/to/anthroalert && .venv/bin/python simple_alert.py
```

### Run Trailing Stop Bot

```bash
# Requires HYPERLIQUID_PRIVATE_KEY in .env
python trailing_stop.py
```

## Configuration

Edit thresholds in `simple_alert.py`:

```python
MIN_VALUE_USD = 5000      # Minimum single trade value
MIN_CLUSTER_VALUE = 20000 # Minimum cluster value
NOTABLE_LABELS = ["Galaxy Digital", "Fund", "Smart Trader"]
```

Trailing stop config in `trailing_stop.py`:

```python
TRAIL_PERCENT = 0.015     # 1.5% trail
ACTIVATION_PERCENT = 0    # Activate immediately (or set to 0.01 for 1% profit)
POLL_INTERVAL = 30        # Check every 30 seconds
```

## Alert Format

```
🟢 #HYPE Perp Alert

⭐ Galaxy Digital

📊 10 trades
💰 $35,000 notional
🎯 LONG @ $36.63

👛 0xcac196...ff26b3

📡 AnthroAlert • Nansen
```

## Architecture

```
anthroalert/
├── simple_alert.py      # Smart money scanner (Nansen CLI)
├── trailing_stop.py     # Trailing stop bot (Hyperliquid CLI)
├── config.py            # Full agent config (optional)
├── agents/              # Modular agent system (optional)
└── .env                 # API keys (not committed)
```

## License

MIT
