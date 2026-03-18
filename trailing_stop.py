#!/usr/bin/env python3
"""
Trailing Stop Loss for Hyperliquid
- 1% trail from peak/trough
- Activates after 1% profit
- Polls every 30s
- Tracks all positions
- Posts updates to Telegram
"""

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

# Config
TRAIL_PERCENT = 0.015  # 1.5%
ACTIVATION_PERCENT = 0  # Activate immediately
POLL_INTERVAL = 30  # seconds
SIGNIFICANT_MOVE = 0.005  # 0.5% move to notify stop adjustment

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


@dataclass
class TrackedPosition:
    coin: str
    side: str  # "long" or "short"
    size: float
    entry_price: float
    highest_price: float = 0.0
    lowest_price: float = float("inf")
    trailing_active: bool = False
    last_stop: float = 0.0
    last_notified_stop: float = 0.0


# Global state
positions: Dict[str, TrackedPosition] = {}


def post_telegram(message: str) -> bool:
    """Post to Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        print(f"[TG] {message}")
        return True
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.json().get("ok", False)
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def run_hl_command(args: list) -> Optional[dict]:
    """Run hyperliquid-cli command and return JSON."""
    cmd = ["hl"] + args + ["--json"]
    env = os.environ.copy()
    env["HYPERLIQUID_PRIVATE_KEY"] = os.getenv("HYPERLIQUID_PRIVATE_KEY", "")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        if result.returncode != 0:
            print(f"HL error: {result.stderr}")
            return None
        return json.loads(result.stdout)
    except Exception as e:
        print(f"HL command failed: {e}")
        return None


def get_positions() -> list:
    """Get current positions from Hyperliquid."""
    data = run_hl_command(["account", "positions"])
    if not data:
        return []
    return data.get("positions", data) if isinstance(data, dict) else data


def get_price(coin: str) -> Optional[float]:
    """Get current price for a coin."""
    data = run_hl_command(["asset", "price", coin])
    if not data:
        return None
    return float(data.get("price", data.get("mid", 0)))


def close_position(coin: str, side: str, size: float) -> bool:
    """Close a position with IOC limit order (market-like execution)."""
    # To close: sell if long, buy if short
    close_side = "sell" if side == "long" else "buy"
    
    # Get current price for IOC limit order
    current_price = get_price(coin)
    if not current_price:
        print(f"Failed to get price for {coin}")
        return False
    
    # Set aggressive limit price with 2% slippage for guaranteed fill
    if close_side == "buy":
        limit_price = current_price * 1.02  # 2% above market to buy
    else:
        limit_price = current_price * 0.98  # 2% below market to sell
    
    # Format price with proper precision (5 sig figs max, strip trailing zeros)
    price_str = f"{limit_price:.5g}"
    size_str = f"{abs(size):.6g}"
    
    # Use IOC limit order instead of market order
    cmd = ["hl", "order", "limit", close_side, size_str, coin, price_str, "--tif", "Ioc", "--reduce-only"]
    print(f"Executing: {' '.join(cmd)}")
    
    env = os.environ.copy()
    env["HYPERLIQUID_PRIVATE_KEY"] = os.getenv("HYPERLIQUID_PRIVATE_KEY", "")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        if result.returncode != 0:
            print(f"Close order error: {result.stderr or result.stdout}")
        return result.returncode == 0
    except Exception as e:
        print(f"Close order failed: {e}")
        return False


def calculate_stop(pos: TrackedPosition, current_price: float) -> float:
    """Calculate current stop loss level.
    
    Profit-only trailing: only trail when stop would be in profit zone.
    - Long: stop must be above entry
    - Short: stop must be below entry
    """
    if pos.side == "long":
        raw_stop = pos.highest_price * (1 - TRAIL_PERCENT)
        # Only trail if stop is above entry (profit zone)
        if raw_stop <= pos.entry_price:
            return 0  # No stop yet, waiting for profit
        return raw_stop
    else:
        raw_stop = pos.lowest_price * (1 + TRAIL_PERCENT)
        # Only trail if stop is below entry (profit zone)
        if raw_stop >= pos.entry_price:
            return float('inf')  # No stop yet, waiting for profit
        return raw_stop


def calculate_pnl_percent(pos: TrackedPosition, current_price: float) -> float:
    """Calculate current PnL percentage."""
    if pos.side == "long":
        return (current_price - pos.entry_price) / pos.entry_price
    else:
        return (pos.entry_price - current_price) / pos.entry_price


def check_stop_triggered(pos: TrackedPosition, current_price: float) -> bool:
    """Check if stop loss is triggered."""
    if not pos.trailing_active:
        return False
    
    stop = calculate_stop(pos, current_price)
    
    # No valid stop yet (waiting for profit zone)
    if (pos.side == "long" and stop == 0) or (pos.side == "short" and stop == float('inf')):
        return False
    
    if pos.side == "long":
        return current_price <= stop
    else:
        return current_price >= stop


def process_position(pos: TrackedPosition, current_price: float) -> None:
    """Process a single position - update tracking and check stops."""
    pnl_pct = calculate_pnl_percent(pos, current_price)
    
    # Check activation
    if not pos.trailing_active:
        if pnl_pct >= ACTIVATION_PERCENT:
            pos.trailing_active = True
            pos.highest_price = current_price
            pos.lowest_price = current_price
            stop = calculate_stop(pos, current_price)
            pos.last_stop = stop
            pos.last_notified_stop = stop
            
            emoji = "🟢" if pos.side == "long" else "🔴"
            
            # Check if stop is valid (in profit zone)
            if (pos.side == "long" and stop == 0) or (pos.side == "short" and stop == float('inf')):
                stop_str = "waiting for profit"
            else:
                stop_str = f"${stop:,.2f}"
            
            post_telegram(
                f"{emoji} *#{pos.coin} Trail Started*\n\n"
                f"Side: {pos.side.upper()}\n"
                f"Size: {pos.size}\n"
                f"Entry: ${pos.entry_price:,.2f}\n"
                f"Current: ${current_price:,.2f}\n"
                f"PnL: {'+' if pnl_pct >= 0 else ''}{pnl_pct*100:.2f}%\n"
                f"Stop: {stop_str} ({TRAIL_PERCENT*100:.1f}% trail)\n\n"
                f"📡 TrailingStop"
            )
        return
    
    # Update peak/trough
    price_updated = False
    if pos.side == "long":
        if current_price > pos.highest_price:
            pos.highest_price = current_price
            price_updated = True
    else:
        if current_price < pos.lowest_price:
            pos.lowest_price = current_price
            price_updated = True
    
    # Calculate new stop
    new_stop = calculate_stop(pos, current_price)
    pos.last_stop = new_stop
    
    # Notify on significant stop movement
    if price_updated and pos.last_notified_stop > 0:
        stop_move = abs(new_stop - pos.last_notified_stop) / pos.last_notified_stop
        if stop_move >= SIGNIFICANT_MOVE:
            emoji = "📈" if pos.side == "long" else "📉"
            direction = "↑" if pos.side == "long" else "↓"
            post_telegram(
                f"{emoji} *#{pos.coin} Stop Moved*\n\n"
                f"Side: {pos.side.upper()}\n"
                f"Price: ${current_price:,.2f}\n"
                f"New Stop: ${new_stop:,.2f} {direction}\n"
                f"PnL: {'+' if pnl_pct >= 0 else ''}{pnl_pct*100:.2f}%\n\n"
                f"📡 TrailingStop"
            )
            pos.last_notified_stop = new_stop
    
    # Check if stop triggered
    if check_stop_triggered(pos, current_price):
        emoji = "🔴" if pos.side == "long" else "🟢"
        post_telegram(
            f"⚠️ *#{pos.coin} Stop Triggered*\n\n"
            f"Side: {pos.side.upper()}\n"
            f"Size: {pos.size}\n"
            f"Entry: ${pos.entry_price:,.2f}\n"
            f"Exit: ${current_price:,.2f}\n"
            f"Stop: ${pos.last_stop:,.2f}\n"
            f"PnL: {'+' if pnl_pct >= 0 else ''}{pnl_pct*100:.2f}%\n\n"
            f"Closing position...\n\n"
            f"📡 TrailingStop"
        )
        
        # Execute close
        success = close_position(pos.coin, pos.side, pos.size)
        
        if success:
            post_telegram(
                f"✅ *#{pos.coin} Position Closed*\n\n"
                f"Final PnL: {'+' if pnl_pct >= 0 else ''}{pnl_pct*100:.2f}%\n\n"
                f"📡 TrailingStop"
            )
            # Remove from tracking
            if pos.coin in positions:
                del positions[pos.coin]
        else:
            post_telegram(
                f"❌ *#{pos.coin} Close Failed*\n\n"
                f"Manual intervention required!\n\n"
                f"📡 TrailingStop"
            )


def sync_positions() -> None:
    """Sync tracked positions with actual positions."""
    global positions
    
    current_positions = get_positions()
    if not current_positions:
        return
    
    # Build set of current position coins
    current_coins = set()
    
    for pos_data in current_positions:
        coin = pos_data.get("coin", pos_data.get("symbol", ""))
        size = float(pos_data.get("szi", pos_data.get("size", 0)))
        
        if size == 0:
            continue
        
        current_coins.add(coin)
        side = "long" if size > 0 else "short"
        entry_price = float(pos_data.get("entryPx", pos_data.get("entry_price", 0)))
        
        # New position
        if coin not in positions:
            positions[coin] = TrackedPosition(
                coin=coin,
                side=side,
                size=abs(size),
                entry_price=entry_price,
                highest_price=entry_price,
                lowest_price=entry_price,
            )
            print(f"Tracking new position: {coin} {side} {size} @ {entry_price}")
        else:
            # Update size if changed
            positions[coin].size = abs(size)
    
    # Remove closed positions
    closed = [c for c in positions if c not in current_coins]
    for coin in closed:
        print(f"Position closed externally: {coin}")
        del positions[coin]


def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Trailing Stop started")
    print(f"Config: {TRAIL_PERCENT*100}% trail, {ACTIVATION_PERCENT*100}% activation, {POLL_INTERVAL}s interval")
    
    post_telegram(
        "🚀 *Trailing Stop Started*\n\n"
        f"Trail: {TRAIL_PERCENT*100:.1f}%\n"
        f"Activation: {ACTIVATION_PERCENT*100:.1f}% profit\n"
        f"Poll: {POLL_INTERVAL}s\n\n"
        f"📡 TrailingStop"
    )
    
    while True:
        try:
            # Sync with actual positions
            sync_positions()
            
            # Process each tracked position
            for coin, pos in list(positions.items()):
                price = get_price(coin)
                if price:
                    process_position(pos, price)
            
            # Status log
            if positions:
                status = ", ".join(
                    f"{c}: {'🟢' if p.trailing_active else '⏳'}"
                    for c, p in positions.items()
                )
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Tracking: {status}")
            
        except Exception as e:
            print(f"Error in main loop: {e}")
        
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
