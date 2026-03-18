#!/usr/bin/env python3
"""
Trailing Stop Loss for Hyperliquid
- Places REAL stop orders on exchange (visible in UI)
- 1.5% trail from peak/trough  
- Updates stop order as price moves favorably
- Polls every 30s
"""

import sys
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, List

import requests as http_requests
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

load_dotenv()

# Config
TRAIL_PERCENT = 0.01  # 1%
ACTIVATION_PERCENT = 0  # Activate immediately (in profit zone)
POLL_INTERVAL = 30  # seconds
SIGNIFICANT_MOVE = 0.005  # 0.5% move to update stop order


def fmt_price(price: float) -> str:
    """Format price based on magnitude: $97.8913, $103.11, $1001"""
    if price >= 1000:
        return f"${price:,.0f}"
    elif price >= 100:
        return f"${price:,.2f}"
    else:
        return f"${price:.4f}"

# Hyperliquid
PRIVATE_KEY = os.getenv("HYPERLIQUID_PRIVATE_KEY", "")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Initialize Hyperliquid clients
account = Account.from_key(PRIVATE_KEY)
ADDRESS = account.address
info = Info(constants.MAINNET_API_URL, skip_ws=True)
exchange = Exchange(account, constants.MAINNET_API_URL)


@dataclass
class TrackedPosition:
    coin: str
    side: str  # "long" or "short"
    size: float
    entry_price: float
    dex: Optional[str] = None  # None for main, "xyz" for xyz DEX
    highest_price: float = 0.0
    lowest_price: float = float("inf")
    trailing_active: bool = False
    last_stop: float = 0.0
    last_notified_stop: float = 0.0
    stop_order_oid: Optional[int] = None


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
        resp = http_requests.post(url, json=payload, timeout=10)
        return resp.json().get("ok", False)
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def get_positions() -> list:
    """Get current positions from Hyperliquid (main + xyz DEX)."""
    result = []
    
    # Query main clearinghouse
    try:
        user_state = info.user_state(ADDRESS)
        for p in user_state.get("assetPositions", []):
            pos = p.get("position", {})
            if pos and float(pos.get("szi", 0)) != 0:
                result.append({
                    "coin": pos.get("coin"),
                    "size": float(pos.get("szi", 0)),
                    "entry_price": float(pos.get("entryPx", 0)),
                    "dex": None,
                })
    except Exception as e:
        print(f"Error getting main positions: {e}")
    
    # Query xyz DEX clearinghouse
    try:
        resp = http_requests.post(
            constants.MAINNET_API_URL + '/info',
            json={'type': 'clearinghouseState', 'user': ADDRESS, 'dex': 'xyz'},
            timeout=10
        )
        data = resp.json()
        for p in data.get("assetPositions", []):
            pos = p.get("position", {})
            if pos and float(pos.get("szi", 0)) != 0:
                result.append({
                    "coin": pos.get("coin"),
                    "size": float(pos.get("szi", 0)),
                    "entry_price": float(pos.get("entryPx", 0)),
                    "dex": "xyz",
                })
    except Exception as e:
        print(f"Error getting xyz positions: {e}")
    
    return result


def get_price(coin: str) -> Optional[float]:
    """Get current mid price for a coin."""
    try:
        all_mids = info.all_mids()
        return float(all_mids.get(coin, 0))
    except Exception as e:
        print(f"Error getting price for {coin}: {e}")
        return None


def get_existing_stops(coin: str, dex: Optional[str] = None) -> List[dict]:
    """Get existing stop orders for a coin."""
    try:
        req = {'type': 'frontendOpenOrders', 'user': ADDRESS}
        if dex:
            req['dex'] = dex
        resp = http_requests.post(
            constants.MAINNET_API_URL + '/info',
            json=req,
            timeout=10
        )
        orders = resp.json()
        return [o for o in orders if o.get('coin') == coin and o.get('isTrigger') and o.get('orderType') == 'Stop Market']
    except Exception as e:
        print(f"Error getting existing stops: {e}")
        return []


def cancel_order(oid: int, coin: str) -> bool:
    """Cancel an order by ID."""
    try:
        result = exchange.cancel(coin, oid)
        print(f"Cancel {oid}: {result.get('status')}")
        return result.get("status") == "ok"
    except Exception as e:
        print(f"Error canceling order {oid}: {e}")
        return False


def place_stop_order(coin: str, side: str, size: float, trigger_price: float, dex: Optional[str] = None) -> Optional[int]:
    """Place a stop-loss order on Hyperliquid. Returns order ID if successful."""
    try:
        # Cancel any existing stops for this coin first
        existing = get_existing_stops(coin, dex)
        for stop in existing:
            cancel_order(stop['oid'], coin)
            time.sleep(0.5)  # Brief pause between cancels
        
        # For closing: buy to close short, sell to close long
        is_buy = side == "short"
        
        # Round prices
        trigger_price = round(trigger_price, 1)
        
        # Limit price with slippage
        if is_buy:
            limit_price = round(trigger_price * 1.02, 1)
        else:
            limit_price = round(trigger_price * 0.98, 1)
        
        print(f"Placing stop: {coin} trigger={trigger_price} limit={limit_price}")
        
        result = exchange.order(
            coin,
            is_buy,
            size,
            limit_price,
            {"trigger": {"triggerPx": trigger_price, "isMarket": True, "tpsl": "sl"}},
            reduce_only=True,
        )
        
        if result.get("status") == "ok":
            statuses = result.get("response", {}).get("data", {}).get("statuses", [])
            if statuses and "resting" in statuses[0]:
                oid = statuses[0]["resting"].get("oid")
                print(f"✅ Stop placed: {coin} @ ${trigger_price} (oid: {oid})")
                return oid
        
        print(f"❌ Failed to place stop: {result}")
        return None
        
    except Exception as e:
        print(f"Error placing stop order: {e}")
        import traceback
        traceback.print_exc()
        return None


def calculate_stop(pos: TrackedPosition, current_price: float) -> float:
    """Calculate current stop loss level."""
    if pos.side == "long":
        raw_stop = pos.highest_price * (1 - TRAIL_PERCENT)
        if raw_stop <= pos.entry_price:
            return 0
        return raw_stop
    else:
        raw_stop = pos.lowest_price * (1 + TRAIL_PERCENT)
        if raw_stop >= pos.entry_price:
            return float('inf')
        return raw_stop


def calculate_pnl_percent(pos: TrackedPosition, current_price: float) -> float:
    """Calculate current PnL percentage."""
    if pos.side == "long":
        return (current_price - pos.entry_price) / pos.entry_price
    else:
        return (pos.entry_price - current_price) / pos.entry_price


def process_position(pos: TrackedPosition, current_price: float) -> None:
    """Process a single position."""
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
            
            if (pos.side == "long" and stop == 0) or (pos.side == "short" and stop == float('inf')):
                stop_str = "waiting for profit"
            else:
                oid = place_stop_order(pos.coin, pos.side, pos.size, stop, pos.dex)
                if oid:
                    pos.stop_order_oid = oid
                    stop_str = f"{fmt_price(stop)} ✅ ON-CHAIN"
                else:
                    stop_str = f"{fmt_price(stop)} (soft - order failed)"
            
            post_telegram(
                f"{emoji} *#{pos.coin} Trail Started*\n\n"
                f"Side: {pos.side.upper()}\n"
                f"Size: {pos.size}\n"
                f"Entry: {fmt_price(pos.entry_price)}\n"
                f"Current: {fmt_price(current_price)}\n"
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
    
    # Update stop order if moved significantly
    if price_updated and pos.last_notified_stop > 0 and new_stop != float('inf') and new_stop != 0:
        stop_move = abs(new_stop - pos.last_notified_stop) / pos.last_notified_stop
        if stop_move >= SIGNIFICANT_MOVE:
            new_oid = place_stop_order(pos.coin, pos.side, pos.size, new_stop, pos.dex)
            if new_oid:
                pos.stop_order_oid = new_oid
                order_status = "on-chain"
            else:
                order_status = "soft"
            
            pos.last_stop = new_stop
            pos.last_notified_stop = new_stop
            
            emoji = "📈" if pos.side == "long" else "📉"
            direction = "↑" if pos.side == "long" else "↓"
            
            post_telegram(
                f"{emoji} *#{pos.coin} Stop Moved*\n\n"
                f"Side: {pos.side.upper()}\n"
                f"Price: {fmt_price(current_price)}\n"
                f"New Stop: {fmt_price(new_stop)} {direction} ({order_status})\n"
                f"PnL: {'+' if pnl_pct >= 0 else ''}{pnl_pct*100:.2f}%\n\n"
                f"📡 TrailingStop"
            )
    
    pos.last_stop = new_stop


def sync_positions() -> None:
    """Sync tracked positions with actual positions."""
    global positions
    
    current_positions = get_positions()
    if not current_positions:
        return
    
    current_coins = set()
    
    for pos_data in current_positions:
        coin = pos_data.get("coin", "")
        size = pos_data.get("size", 0)
        
        if size == 0:
            continue
        
        current_coins.add(coin)
        side = "long" if size > 0 else "short"
        entry_price = pos_data.get("entry_price", 0)
        
        if coin not in positions:
            dex = pos_data.get("dex")
            positions[coin] = TrackedPosition(
                coin=coin,
                side=side,
                size=abs(size),
                entry_price=entry_price,
                dex=dex,
                highest_price=entry_price,
                lowest_price=entry_price,
            )
            dex_str = f" (dex={dex})" if dex else ""
            print(f"Tracking new position: {coin} {side} {size} @ {entry_price}{dex_str}")
        else:
            positions[coin].size = abs(size)
    
    closed = [c for c in positions if c not in current_coins]
    for coin in closed:
        pos = positions[coin]
        print(f"Position closed: {coin}")
        
        # Notify about position closure (likely SL hit)
        if pos.trailing_active and pos.stop_order_oid:
            post_telegram(
                f"🛑 *#{coin} STOP EXECUTED*\n\n"
                f"Side: {pos.side.upper()}\n"
                f"Size: {pos.size}\n"
                f"Entry: {fmt_price(pos.entry_price)}\n"
                f"Stop was: {fmt_price(pos.last_stop)}\n\n"
                f"Position closed by on-chain stop order.\n\n"
                f"📡 TrailingStop"
            )
        
        del positions[coin]


def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Trailing Stop started")
    print(f"Config: {TRAIL_PERCENT*100}% trail, {ACTIVATION_PERCENT*100}% activation, {POLL_INTERVAL}s interval")
    print(f"Address: {ADDRESS}")
    print(f"Mode: ✅ ON-CHAIN stops via Hyperliquid SDK")
    
    post_telegram(
        "🚀 *Trailing Stop Started*\n\n"
        f"Trail: {TRAIL_PERCENT*100:.1f}%\n"
        f"Activation: In profit zone\n"
        f"Poll: {POLL_INTERVAL}s\n"
        f"Mode: ✅ ON-CHAIN stops\n\n"
        f"📡 TrailingStop"
    )
    
    while True:
        try:
            sync_positions()
            
            for coin, pos in list(positions.items()):
                price = get_price(coin)
                if price:
                    process_position(pos, price)
            
            if positions:
                status_parts = []
                for c, p in positions.items():
                    if p.trailing_active:
                        if (p.side == "long" and p.last_stop == 0) or (p.side == "short" and p.last_stop == float('inf')):
                            status_parts.append(f"{c}: ⏳")
                        else:
                            chain = "✅" if p.stop_order_oid else "⚠️"
                            status_parts.append(f"{c}: {chain} SL@{p.last_stop:.2f}")
                    else:
                        status_parts.append(f"{c}: ⏳")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {', '.join(status_parts)}")
            
        except Exception as e:
            print(f"Error in main loop: {e}")
            import traceback
            traceback.print_exc()
        
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
