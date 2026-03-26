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
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, List

import requests as http_requests
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

load_dotenv()

# Config
TRAIL_PERCENT = 0.005  # 0.5%
ACTIVATION_PERCENT = 0  # Activate immediately (in profit zone)
POLL_INTERVAL = 30  # seconds
SIGNIFICANT_MOVE = 0.005  # 0.5% move to update stop order

# Self-healing config
MAX_CONSECUTIVE_FAILURES = 5
VERIFY_INTERVAL = 5  # Verify stops every N poll cycles
STATE_FILE = Path(__file__).parent / "data" / "trailing_state.json"


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
if not PRIVATE_KEY:
    sys.exit("HYPERLIQUID_PRIVATE_KEY not set in .env")
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
consecutive_failures: int = 0
poll_count: int = 0


def save_state() -> None:
    """Save tracking state to disk."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        state = {}
        for key, pos in positions.items():
            state[key] = {
                "coin": pos.coin,
                "side": pos.side,
                "size": pos.size,
                "entry_price": pos.entry_price,
                "dex": pos.dex,
                "highest_price": pos.highest_price,
                "lowest_price": pos.lowest_price if pos.lowest_price != float("inf") else None,
                "trailing_active": pos.trailing_active,
                "last_stop": pos.last_stop if pos.last_stop != float("inf") else None,
                "last_notified_stop": pos.last_notified_stop,
                "stop_order_oid": pos.stop_order_oid,
            }
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        print(f"Error saving state: {e}")


def load_state() -> Dict[str, TrackedPosition]:
    """Load tracking state from disk."""
    if not STATE_FILE.exists():
        return {}
    
    try:
        data = json.loads(STATE_FILE.read_text())
        loaded = {}
        for key, s in data.items():
            loaded[key] = TrackedPosition(
                coin=s["coin"],
                side=s["side"],
                size=s["size"],
                entry_price=s["entry_price"],
                dex=s.get("dex"),
                highest_price=s.get("highest_price", s["entry_price"]),
                lowest_price=s.get("lowest_price") if s.get("lowest_price") is not None else float("inf"),
                trailing_active=s.get("trailing_active", False),
                last_stop=s.get("last_stop") if s.get("last_stop") is not None else 0.0,
                last_notified_stop=s.get("last_notified_stop", 0.0),
                stop_order_oid=s.get("stop_order_oid"),
            )
        print(f"Loaded state for {len(loaded)} positions")
        return loaded
    except Exception as e:
        print(f"Error loading state: {e}")
        return {}


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
        raw = all_mids.get(coin)
        if raw is None:
            return None
        return float(raw)
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


def verify_stop_exists(oid: int) -> bool:
    """Verify a stop order still exists on-chain."""
    try:
        orders = info.open_orders(ADDRESS)
        return any(o.get('oid') == oid for o in orders)
    except Exception as e:
        print(f"Error verifying stop: {e}")
        return False


def get_all_stop_oids() -> set:
    """Get all current stop order IDs on-chain."""
    try:
        orders = info.open_orders(ADDRESS)
        return {o.get('oid') for o in orders if o.get('orderType') in ['Stop Market', 'Stop Limit']}
    except Exception as e:
        print(f"Error getting stop oids: {e}")
        return set()


def verify_all_stops() -> None:
    """Verify all tracked stops exist on-chain, re-place if missing."""
    global positions
    
    on_chain_oids = get_all_stop_oids()
    
    for key, pos in positions.items():
        if pos.stop_order_oid and pos.stop_order_oid not in on_chain_oids:
            print(f"⚠️ Stop missing for {pos.coin} (oid {pos.stop_order_oid}), re-placing...")
            pos.stop_order_oid = None  # Force re-place
            
            if pos.trailing_active and pos.last_stop > 0 and pos.last_stop != float('inf'):
                new_oid = place_stop_order(pos.coin, pos.side, pos.size, pos.last_stop, pos.dex)
                if new_oid:
                    pos.stop_order_oid = new_oid
                    print(f"✅ Stop re-placed for {pos.coin} @ {pos.last_stop}")


def place_stop_order(coin: str, side: str, size: float, trigger_price: float, dex: Optional[str] = None) -> Optional[int]:
    """Place a stop-loss order on Hyperliquid. Returns order ID if successful."""
    global consecutive_failures
    
    try:
        # Cancel any existing stops for this coin first
        existing = get_existing_stops(coin, dex)
        for stop in existing:
            cancel_order(stop['oid'], coin)
            time.sleep(0.5)  # Brief pause between cancels
        
        # For closing: buy to close short, sell to close long
        is_buy = side == "short"
        
        # Round prices - BTC needs integer, others need 1 decimal
        if coin == "BTC":
            trigger_price = round(trigger_price)
            if is_buy:
                limit_price = round(trigger_price * 1.02)
            else:
                limit_price = round(trigger_price * 0.98)
        else:
            trigger_price = round(trigger_price, 1)
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
                
                # Verify the stop actually exists
                time.sleep(1)
                if verify_stop_exists(oid):
                    consecutive_failures = 0  # Reset on success
                    return oid
                else:
                    print(f"⚠️ Stop placed but not found on-chain!")
                    consecutive_failures += 1
                    return None
        
        print(f"❌ Failed to place stop: {result}")
        consecutive_failures += 1
        return None
        
    except Exception as e:
        print(f"Error placing stop order: {e}")
        import traceback
        traceback.print_exc()
        consecutive_failures += 1
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
                # Not in profit zone yet, no notification
                pass
            else:
                oid = place_stop_order(pos.coin, pos.side, pos.size, stop, pos.dex)
                if oid:
                    pos.stop_order_oid = oid
                    stop_str = f"{fmt_price(stop)} ✅ ON-CHAIN"
                else:
                    stop_str = f"{fmt_price(stop)} (soft - order failed)"
                
                # Only notify when stop is actually placed
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


def _pos_key(coin: str, dex: Optional[str]) -> str:
    """Build a unique position key from coin and dex."""
    return f"{coin}:{dex}" if dex else coin


def sync_positions() -> None:
    """Sync tracked positions with actual positions."""
    global positions

    current_positions = get_positions()
    if not current_positions:
        return

    current_keys = set()

    for pos_data in current_positions:
        coin = pos_data.get("coin", "")
        size = pos_data.get("size", 0)

        if size == 0:
            continue

        dex = pos_data.get("dex")
        key = _pos_key(coin, dex)
        current_keys.add(key)
        side = "long" if size > 0 else "short"
        entry_price = pos_data.get("entry_price", 0)

        if key not in positions:
            positions[key] = TrackedPosition(
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
            existing = positions[key]
            if existing.side != side or existing.entry_price != entry_price:
                # Position flipped or re-entered — reset tracking
                print(f"Position changed: {coin} {existing.side}→{side} @ {entry_price}")
                positions[key] = TrackedPosition(
                    coin=coin,
                    side=side,
                    size=abs(size),
                    entry_price=entry_price,
                    dex=dex,
                    highest_price=entry_price,
                    lowest_price=entry_price,
                )
            else:
                existing.size = abs(size)

    closed = [k for k in positions if k not in current_keys]
    for key in closed:
        pos = positions[key]
        print(f"Position closed: {pos.coin}")

        # Notify about position closure (likely SL hit)
        if pos.trailing_active and pos.stop_order_oid:
            post_telegram(
                f"🛑 *#{pos.coin} STOP EXECUTED*\n\n"
                f"Side: {pos.side.upper()}\n"
                f"Size: {pos.size}\n"
                f"Entry: {fmt_price(pos.entry_price)}\n"
                f"Stop was: {fmt_price(pos.last_stop)}\n\n"
                f"Position closed by on-chain stop order.\n\n"
                f"📡 TrailingStop"
            )

        del positions[key]
    
    # Save state after any changes
    if current_keys or closed:
        save_state()


def restart_self():
    """Restart the script."""
    print("🔄 Too many failures, restarting...")
    post_telegram(
        "🔄 *Trailing Stop Restarting*\n\n"
        f"Reason: {MAX_CONSECUTIVE_FAILURES} consecutive failures\n"
        f"Auto-recovery in progress...\n\n"
        f"📡 TrailingStop"
    )
    time.sleep(2)
    os.execv(sys.executable, ['python'] + sys.argv)


def main():
    global consecutive_failures, poll_count, positions
    
    print(f"[{datetime.now(timezone.utc).isoformat()}] Trailing Stop started")
    print(f"Config: {TRAIL_PERCENT*100}% trail, {ACTIVATION_PERCENT*100}% activation, {POLL_INTERVAL}s interval")
    print(f"Address: {ADDRESS}")
    print(f"Mode: ✅ ON-CHAIN stops via Hyperliquid SDK")
    print(f"Self-healing: restart after {MAX_CONSECUTIVE_FAILURES} failures, verify every {VERIFY_INTERVAL} cycles")
    
    # Load saved state
    positions = load_state()
    
    post_telegram(
        "🚀 *Trailing Stop Started*\n\n"
        f"Trail: {TRAIL_PERCENT*100:.1f}%\n"
        f"Activation: In profit zone\n"
        f"Poll: {POLL_INTERVAL}s\n"
        f"Mode: ✅ ON-CHAIN stops\n"
        f"Self-healing: ✅ enabled\n\n"
        f"📡 TrailingStop"
    )
    
    while True:
        try:
            poll_count += 1
            
            # Check if we need to self-heal
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                restart_self()
            
            sync_positions()
            
            # Periodic verification of all stops
            if poll_count % VERIFY_INTERVAL == 0:
                verify_all_stops()
            
            for key, pos in list(positions.items()):
                price = get_price(pos.coin)
                if price:
                    process_position(pos, price)
                else:
                    # Price fetch failed
                    consecutive_failures += 1
            
            # Save state after processing
            if positions:
                save_state()

            if positions:
                status_parts = []
                for key, p in positions.items():
                    label = p.coin if not p.dex else f"{p.coin}:{p.dex}"
                    if p.trailing_active:
                        if (p.side == "long" and p.last_stop == 0) or (p.side == "short" and p.last_stop == float('inf')):
                            status_parts.append(f"{label}: ⏳")
                        else:
                            chain = "✅" if p.stop_order_oid else "⚠️"
                            status_parts.append(f"{label}: {chain} SL@{p.last_stop:.2f}")
                    else:
                        status_parts.append(f"{label}: ⏳")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {', '.join(status_parts)}")
            
            # Reset failure count on successful loop with positions
            if positions:
                # Only reset if we successfully processed at least one position with a price
                pass  # Failures reset happens in place_stop_order on success
            
        except Exception as e:
            print(f"Error in main loop: {e}")
            import traceback
            traceback.print_exc()
            consecutive_failures += 1
        
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
