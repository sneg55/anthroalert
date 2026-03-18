# AnthroAlert

Smart money perp trade alerts for Hyperliquid. Powered by [Nansen CLI](https://github.com/nansen-ai/nansen-cli).

Get notified when whales and labeled wallets (Galaxy Digital, funds, smart traders) make significant moves.

## How It Works

### Smart Money Scanner (`simple_alert.py`)

The scanner fetches the latest 100 perpetual trades from Nansen's smart money dataset and applies threshold-based filtering:

```
┌─────────────────────────────────────────────────────────────┐
│  Nansen API → 100 recent perp trades                        │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  CLUSTER TRADES                                              │
│  Group by: wallet + token + side (long/short)               │
│  Sum total notional value per cluster                       │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  APPLY THRESHOLDS                                            │
│                                                              │
│  Alert if:                                                  │
│  • Cluster value ≥ $20,000 (any wallet)                     │
│  • Cluster value ≥ $5,000 AND wallet has notable label      │
│                                                              │
│  Notable labels: "Galaxy Digital", "Fund", "Smart Trader"  │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  FORMAT & POST                                               │
│  • Calculate average fill price                             │
│  • Generate hashtag from ticker                             │
│  • Post top 3 alerts to Telegram                            │
└─────────────────────────────────────────────────────────────┘
```

**Why clustering?** Whales often split large orders into multiple trades. A single $50k position might show up as 10 separate $5k fills. Clustering catches the full picture.

### Trailing Stop Bot (`trailing_stop.py`)

Automated stop-loss management for your Hyperliquid positions:

```
┌─────────────────────────────────────────────────────────────┐
│  POSITION SYNC (every 30s)                                   │
│  Fetch open positions from Hyperliquid                      │
│  Track new positions, remove closed ones                    │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  TRAILING LOGIC                                              │
│                                                              │
│  Long positions:                                            │
│  • Track highest price since entry                          │
│  • Stop = highest × (1 - trail%)                            │
│  • Only activate when stop > entry (profit zone)            │
│                                                              │
│  Short positions:                                           │
│  • Track lowest price since entry                           │
│  • Stop = lowest × (1 + trail%)                             │
│  • Only activate when stop < entry (profit zone)            │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  EXECUTION                                                   │
│  If price crosses stop → close with IOC limit order         │
│  2% slippage tolerance for guaranteed fill                  │
└─────────────────────────────────────────────────────────────┘
```

**Profit-only trailing:** Stops only become active once you're in profit. No premature exits on initial volatility.

## Installation

### Prerequisites

```bash
# Install Nansen CLI
npm install -g nansen-cli

# Install Hyperliquid CLI (for trailing stops)
npm install -g @chrisling-dev/hyperliquid-cli
```

### Setup

```bash
# Clone
git clone https://github.com/nicksawinyh/anthroalert.git
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

## Usage

### Run Smart Money Alerts

```bash
# Single run
python simple_alert.py

# Schedule with cron (every 30 min)
*/30 * * * * cd /path/to/anthroalert && source .venv/bin/activate && python simple_alert.py
```

### Run Trailing Stop Bot

```bash
# Requires HYPERLIQUID_PRIVATE_KEY in .env
python trailing_stop.py
```

The bot runs continuously, polling every 30 seconds.

## Configuration

### Alert Thresholds

Edit `simple_alert.py`:

```python
MIN_VALUE_USD = 5000       # Minimum trade value for notable wallets
MIN_CLUSTER_VALUE = 20000  # Minimum cluster value for any wallet
NOTABLE_LABELS = ["Galaxy Digital", "Fund", "Smart Trader"]
```

### Trailing Stop Settings

Edit `trailing_stop.py`:

```python
TRAIL_PERCENT = 0.015      # 1.5% trail from peak
ACTIVATION_PERCENT = 0     # Activate immediately (or 0.01 for 1% profit)
POLL_INTERVAL = 30         # Check every 30 seconds
SIGNIFICANT_MOVE = 0.005   # 0.5% move triggers stop update notification
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

- 🟢 = Long position
- 🔴 = Short position  
- ⭐ = Notable labeled wallet

## Project Structure

```
anthroalert/
├── simple_alert.py      # Smart money scanner (main script)
├── trailing_stop.py     # Trailing stop bot
├── requirements.txt     # Python dependencies
├── .env.example         # Configuration template
├── .gitignore          # Git ignore rules
├── agents/             # Experimental modular agent system
│   ├── base_agent.py
│   ├── smart_money_agent.py
│   └── sentiment_agent.py
├── config.py           # Full config for agent system
├── main.py             # Agent orchestrator
└── data/               # Local data storage
```

## Requirements

- Python 3.10+
- [Nansen CLI](https://github.com/nansen-ai/nansen-cli) with valid API key
- [Hyperliquid CLI](https://github.com/chrisling-dev/hyperliquid-cli) (for trailing stops)
- Telegram bot (create via [@BotFather](https://t.me/BotFather))

## Disclaimer

This software is for educational purposes only. Trading perpetuals involves significant risk. The authors are not responsible for any financial losses. Use at your own risk.

## License

MIT
