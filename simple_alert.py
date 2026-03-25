#!/usr/bin/env python3
"""Simple AnthroAlert runner - threshold-based, no Claude needed."""

import json
import os
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

NANSEN_API_KEY = os.getenv("NANSEN_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Thresholds
MIN_VALUE_USD = 5000  # Minimum single trade value
MIN_CLUSTER_VALUE = 20000  # Minimum cluster value (same wallet, same direction)
NOTABLE_LABELS = ["Galaxy Digital", "Fund", "Smart Trader"]

# Cache settings
CACHE_DIR = Path(__file__).parent / "data" / "trader_cache"
CACHE_TTL_DAYS = 7


def get_cache_path(trader_address: str) -> Path:
    """Get cache file path for a trader."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Use first 10 and last 6 chars of address as filename
    addr_short = f"{trader_address[:10]}_{trader_address[-6:]}"
    return CACHE_DIR / f"{addr_short}.json"


def load_cached_pnl(trader_address: str) -> dict | None:
    """Load cached PnL data if still valid."""
    cache_path = get_cache_path(trader_address)
    if not cache_path.exists():
        return None
    
    try:
        with open(cache_path) as f:
            data = json.load(f)
        
        # Check if cache is still valid
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
        if datetime.now(timezone.utc) - cached_at > timedelta(days=CACHE_TTL_DAYS):
            return None
        
        return data
    except Exception as e:
        print(f"Cache read error: {e}")
        return None


def save_cached_pnl(trader_address: str, pnl_data: dict):
    """Save PnL data to cache."""
    cache_path = get_cache_path(trader_address)
    try:
        pnl_data["cached_at"] = datetime.now(timezone.utc).isoformat()
        pnl_data["trader_address"] = trader_address
        with open(cache_path, "w") as f:
            json.dump(pnl_data, f, indent=2)
    except Exception as e:
        print(f"Cache write error: {e}")


def verify_trader_position_side(trader_address: str, token_symbol: str) -> str | None:
    """Verify the trader's current position side for a token.
    
    Returns 'Long', 'Short', or None if no position.
    """
    try:
        resp = requests.post(
            "https://api.nansen.ai/api/v1/tgm/perp-positions",
            headers={"apiKey": NANSEN_API_KEY, "Content-Type": "application/json"},
            json={
                "token_symbol": token_symbol,
                "filters": {"address": trader_address},
                "pagination": {"page": 1, "per_page": 1},
            },
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("data"):
                return data["data"][0].get("side")
    except Exception as e:
        print(f"  Position verify error: {e}")
    return None


def fetch_trader_position(trader_address: str, token_symbol: str, expected_side: str) -> dict | None:
    """Fetch current position details for a trader on a specific token.
    
    Only returns data if the position side matches expected_side to avoid
    showing stale/mismatched leverage info.
    """
    print(f"  Fetching position for {trader_address[:10]}... on {token_symbol}")
    
    try:
        resp = requests.post(
            "https://api.nansen.ai/api/v1/tgm/perp-positions",
            headers={"apiKey": NANSEN_API_KEY, "Content-Type": "application/json"},
            json={
                "token_symbol": token_symbol,
                "filters": {"address": trader_address},
                "pagination": {"page": 1, "per_page": 1},
            },
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("data"):
                record = data["data"][0]
                # Only return if side matches what we're alerting
                position_side = record.get("side", "")
                if position_side.lower() != expected_side.lower():
                    print(f"  Position side mismatch: {position_side} vs {expected_side}")
                    return None
                return {
                    "leverage": record.get("leverage"),
                    "leverage_type": record.get("leverage_type"),
                    "entry_price": record.get("entry_price"),
                    "mark_price": record.get("mark_price"),
                    "liquidation_price": record.get("liquidation_price"),
                    "position_value_usd": record.get("position_value_usd"),
                    "upnl_usd": record.get("upnl_usd"),
                }
    except Exception as e:
        print(f"  Position fetch error: {e}")
    
    return None


def fetch_trader_pnl(trader_address: str) -> dict | None:
    """Fetch 7D and 30D PnL for a trader from Nansen API."""
    # Check cache first
    cached = load_cached_pnl(trader_address)
    if cached:
        print(f"  Using cached PnL for {trader_address[:10]}...")
        return cached
    
    print(f"  Fetching PnL for {trader_address[:10]}...")
    
    now = datetime.now(timezone.utc)
    results = {"pnl_7d": None, "roi_7d": None, "pnl_30d": None, "roi_30d": None}
    
    # Fetch 7D PnL
    try:
        resp = requests.post(
            "https://api.nansen.ai/api/v1/tgm/perp-pnl-leaderboard",
            headers={"apiKey": NANSEN_API_KEY, "Content-Type": "application/json"},
            json={
                "token_symbol": "BTC",  # We query BTC but filter by trader
                "date": {
                    "from": (now - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z"),
                    "to": now.strftime("%Y-%m-%dT23:59:59Z"),
                },
                "filters": {"trader_address": trader_address},
                "pagination": {"page": 1, "per_page": 1},
            },
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("data"):
                record = data["data"][0]
                results["pnl_7d"] = record.get("pnl_usd_realised", 0)
                results["roi_7d"] = record.get("roi_percent_realised", 0)
    except Exception as e:
        print(f"  7D PnL fetch error: {e}")
    
    # Small delay to avoid rate limits
    time.sleep(0.5)
    
    # Fetch 30D PnL
    try:
        resp = requests.post(
            "https://api.nansen.ai/api/v1/tgm/perp-pnl-leaderboard",
            headers={"apiKey": NANSEN_API_KEY, "Content-Type": "application/json"},
            json={
                "token_symbol": "BTC",
                "date": {
                    "from": (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z"),
                    "to": now.strftime("%Y-%m-%dT23:59:59Z"),
                },
                "filters": {"trader_address": trader_address},
                "pagination": {"page": 1, "per_page": 1},
            },
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("data"):
                record = data["data"][0]
                results["pnl_30d"] = record.get("pnl_usd_realised", 0)
                results["roi_30d"] = record.get("roi_percent_realised", 0)
    except Exception as e:
        print(f"  30D PnL fetch error: {e}")
    
    # Cache results if we got any data
    if results["pnl_7d"] is not None or results["pnl_30d"] is not None:
        save_cached_pnl(trader_address, results)
    
    return results


def fetch_perp_trades():
    """Fetch smart money perp trades from Nansen CLI."""
    env = os.environ.copy()
    env["NANSEN_API_KEY"] = NANSEN_API_KEY
    
    cmd = ["nansen", "research", "smart-money", "perp-trades", "--limit", "100", "--pretty=false"]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
    
    if result.returncode != 0:
        print(f"Nansen CLI error: {result.stderr}")
        return []
    
    data = json.loads(result.stdout)
    return data.get("data", {}).get("data", [])


def analyze_trades(trades):
    """Find alert-worthy signals using simple threshold logic."""
    alerts = []
    
    # Group by wallet + token + side
    clusters = {}
    for trade in trades:
        key = (trade["trader_address"], trade["token_symbol"], trade["side"])
        if key not in clusters:
            clusters[key] = {
                "label": trade["trader_address_label"],
                "address": trade["trader_address"],
                "token": trade["token_symbol"],
                "side": trade["side"],
                "trades": [],
                "total_value": 0,
            }
        clusters[key]["trades"].append(trade)
        clusters[key]["total_value"] += trade["value_usd"]
    
    # Filter by thresholds
    for key, cluster in clusters.items():
        # Check if notable label
        is_notable = any(label in cluster["label"] for label in NOTABLE_LABELS)
        
        # Large single trade
        max_trade = max(t["value_usd"] for t in cluster["trades"])
        
        # Alert conditions
        if cluster["total_value"] >= MIN_CLUSTER_VALUE or (is_notable and cluster["total_value"] >= MIN_VALUE_USD):
            # Verify current position side matches trade side
            # Trade "Long" = buying = should result in Long position
            # Trade "Short" = selling = should result in Short position
            actual_side = verify_trader_position_side(cluster["address"], cluster["token"])
            
            if actual_side is None:
                print(f"  Skipping {cluster['token']} - no current position (likely closed)")
                continue
            
            if actual_side.lower() != cluster["side"].lower():
                print(f"  Skipping {cluster['token']} - trade side {cluster['side']} but position is {actual_side}")
                continue
            
            # Calculate average fill price
            total_size = sum(t["token_amount"] for t in cluster["trades"])
            avg_price = cluster["total_value"] / total_size if total_size > 0 else 0
            
            alerts.append({
                "label": cluster["label"],
                "address": cluster["address"],
                "address_short": cluster["address"][:10] + "..." + cluster["address"][-6:],
                "token": cluster["token"],
                "token_raw": cluster["token"],  # Keep original for API calls
                "side": actual_side,  # Use verified position side
                "trade_count": len(cluster["trades"]),
                "total_value": cluster["total_value"],
                "avg_price": avg_price,
                "is_notable": is_notable,
            })
    
    return sorted(alerts, key=lambda x: -x["total_value"])


def format_pnl(pnl: float | None, roi: float | None) -> str:
    """Format PnL with ROI."""
    if pnl is None:
        return "N/A"
    
    # Format PnL
    if abs(pnl) >= 1_000_000:
        pnl_str = f"${pnl/1_000_000:+,.1f}M"
    elif abs(pnl) >= 1_000:
        pnl_str = f"${pnl/1_000:+,.0f}K"
    else:
        pnl_str = f"${pnl:+,.0f}"
    
    # Add ROI if available
    if roi is not None:
        return f"{pnl_str} ({roi:+.1f}%)"
    return pnl_str


def format_price_smart(price: float | None) -> str:
    """Format price based on magnitude."""
    if price is None:
        return "N/A"
    if price >= 1000:
        return f"${price:,.0f}"
    elif price >= 1:
        return f"${price:,.2f}"
    elif price >= 0.01:
        return f"${price:.4f}"
    else:
        return f"${price:.6f}"


def format_alert(alert, pnl_data: dict | None = None, position_data: dict | None = None):
    """Format alert for Telegram."""
    emoji = "🟢" if alert["side"] == "Long" else "🔴"
    notable = "⭐ " if alert["is_notable"] else ""
    
    # Extract clean ticker for hashtag
    token = alert['token']
    if ":" in token:
        ticker = token.split(":")[-1]
    else:
        ticker = token
    
    # Format price
    price_str = format_price_smart(alert['avg_price'])
    
    # Build position section (leverage + liquidation)
    position_section = ""
    if position_data and position_data.get("leverage"):
        lev = position_data["leverage"]
        lev_type = position_data.get("leverage_type", "")
        liq = position_data.get("liquidation_price")
        liq_str = format_price_smart(liq) if liq else "N/A"
        position_section = f"⚡ {lev} {lev_type} | Liq: {liq_str}\n"
    
    # Build PnL section
    pnl_section = ""
    if pnl_data:
        pnl_7d = format_pnl(pnl_data.get("pnl_7d"), pnl_data.get("roi_7d"))
        pnl_30d = format_pnl(pnl_data.get("pnl_30d"), pnl_data.get("roi_30d"))
        pnl_section = f"\n📈 7D: {pnl_7d}\n📈 30D: {pnl_30d}\n"
    
    return (
        f"{emoji} *#{ticker} Perp Alert*\n\n"
        f"{notable}*{alert['label'].split('[')[0].strip()}*\n\n"
        f"📊 {alert['trade_count']} trades\n"
        f"💰 ${alert['total_value']:,.0f} notional\n"
        f"🎯 {alert['side'].upper()} @ {price_str}\n"
        f"{position_section}"
        f"{pnl_section}\n"
        f"👛 [#{alert['address_short']}](https://hyperdash.com/address/{alert['address']})\n\n"
        f"📡 AnthroAlert • Nansen"
    )


def post_telegram(message):
    """Post to Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    
    resp = requests.post(url, json=payload, timeout=30)
    return resp.json().get("ok", False)


def check_user_present() -> bool:
    """Check if user is present using presence detection."""
    try:
        from presence import is_user_present
        return is_user_present()
    except ImportError:
        print("Presence module not found, assuming present")
        return True
    except Exception as e:
        print(f"Presence check error: {e}, assuming present")
        return True


def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] AnthroAlert running...")
    
    # Check if user is present before scanning
    if not check_user_present():
        print("User not present - skipping alert scan")
        return
    
    # Fetch trades
    trades = fetch_perp_trades()
    print(f"Fetched {len(trades)} trades")
    
    if not trades:
        print("No trades found")
        return
    
    # Analyze
    alerts = analyze_trades(trades)
    print(f"Found {len(alerts)} alert-worthy signals")
    
    # Post top 3 with PnL and position enrichment
    for alert in alerts[:3]:
        # Fetch PnL data (uses cache if available)
        pnl_data = fetch_trader_pnl(alert["address"])
        
        # Fetch current position data (leverage, liquidation) - only if side matches
        position_data = fetch_trader_position(alert["address"], alert["token_raw"], alert["side"])
        
        message = format_alert(alert, pnl_data, position_data)
        success = post_telegram(message)
        print(f"Posted {alert['token']} {alert['side']}: {'✓' if success else '✗'}")
        
        # Small delay between posts
        time.sleep(1)


if __name__ == "__main__":
    main()
