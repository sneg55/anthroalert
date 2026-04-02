#!/bin/bash
# Health check and start script for trailing stop bot
# Verifies venv works, repairs if needed, then starts/checks bot

set -e
cd "$(dirname "$0")"

VENV_DIR=".venv"
LOG_FILE="logs/trailing_stop.log"
REQUIREMENTS="requirements.txt"

# Ensure logs dir exists
mkdir -p logs

# Check if venv python works
check_venv() {
    if [ ! -f "$VENV_DIR/bin/python" ]; then
        return 1
    fi
    # Try to run python
    "$VENV_DIR/bin/python" -c "import sys; print(sys.version)" >/dev/null 2>&1
}

# Repair venv if broken
repair_venv() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Repairing venv..."
    rm -rf "$VENV_DIR"
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install -q -r "$REQUIREMENTS"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Venv repaired"
}

# Check if bot is running and healthy
check_bot() {
    local pid=$(pgrep -f "trailing_stop.py" 2>/dev/null || true)
    if [ -z "$pid" ]; then
        return 1
    fi
    # Check for recent errors (last 50 lines)
    local errors=$(tail -50 "$LOG_FILE" 2>/dev/null | grep -c -E "Error|Failed" || true)
    errors=${errors:-0}
    if [ "$errors" -gt 5 ]; then
        echo "Bot has $errors errors, needs restart"
        return 1
    fi
    echo "Bot running (PID $pid), $errors errors"
    return 0
}

# Start the bot
start_bot() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting trailing stop bot..."
    pkill -f "trailing_stop.py" 2>/dev/null || true
    sleep 1
    nohup "$VENV_DIR/bin/python" trailing_stop.py >> "$LOG_FILE" 2>&1 &
    sleep 3
    local pid=$(pgrep -f "trailing_stop.py" 2>/dev/null || true)
    if [ -n "$pid" ]; then
        echo "Bot started (PID $pid)"
    else
        echo "Failed to start bot!"
        exit 1
    fi
}

# Main
echo "=== AnthroAlert Health Check ==="

# Step 1: Check/repair venv
if ! check_venv; then
    echo "Venv broken, repairing..."
    repair_venv
fi

# Step 2: Check/start bot
if ! check_bot; then
    start_bot
fi

echo "=== Health check complete ==="
