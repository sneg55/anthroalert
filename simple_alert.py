#!/usr/bin/env python3
"""Simple AnthroAlert runner - threshold-based, no Claude needed."""

import json
import os
import subprocess
from datetime import datetime, timezone

# Load env
from dotenv import load_dotenv
load_dotenv()

NANSEN_API_KEY = os.getenv("NANSEN_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Thresholds
MIN_VALUE_USD = 5000  # Minimum single trade value
MIN_CLUSTER_VALUE = 20000  # Minimum cluster value (same wallet, same direction)
NOTABLE_LABELS = ["Galaxy Digital", "Fund", "Smart Trader"]


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
            # Calculate average fill price
            total_size = sum(t["token_amount"] for t in cluster["trades"])
            avg_price = cluster["total_value"] / total_size if total_size > 0 else 0
            
            alerts.append({
                "label": cluster["label"],
                "address": cluster["address"][:10] + "..." + cluster["address"][-6:],
                "token": cluster["token"],
                "side": cluster["side"],
                "trade_count": len(cluster["trades"]),
                "total_value": cluster["total_value"],
                "avg_price": avg_price,
                "is_notable": is_notable,
            })
    
    return sorted(alerts, key=lambda x: -x["total_value"])


def format_alert(alert):
    """Format alert for Telegram."""
    emoji = "🟢" if alert["side"] == "Long" else "🔴"
    notable = "⭐ " if alert["is_notable"] else ""
    
    # Extract clean ticker for hashtag (e.g., "xyz:SILVER" -> "SILVER", "HYPE" -> "HYPE")
    token = alert['token']
    if ":" in token:
        ticker = token.split(":")[-1]
    else:
        ticker = token
    
    # Format price (handle very small prices like memecoins)
    price = alert['avg_price']
    if price >= 1:
        price_str = f"${price:,.2f}"
    elif price >= 0.01:
        price_str = f"${price:.4f}"
    else:
        price_str = f"${price:.6f}"
    
    return (
        f"{emoji} *#{ticker} Perp Alert*\n\n"
        f"{notable}*{alert['label'].split('[')[0].strip()}*\n\n"
        f"📊 {alert['trade_count']} trades\n"
        f"💰 ${alert['total_value']:,.0f} notional\n"
        f"🎯 {alert['side'].upper()} @ {price_str}\n\n"
        f"👛 `{alert['address']}`\n\n"
        f"📡 AnthroAlert • Nansen"
    )


def post_telegram(message):
    """Post to Telegram."""
    import requests
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    
    resp = requests.post(url, json=payload, timeout=30)
    return resp.json().get("ok", False)


def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] AnthroAlert running...")
    
    # Fetch
    trades = fetch_perp_trades()
    print(f"Fetched {len(trades)} trades")
    
    if not trades:
        print("No trades found")
        return
    
    # Analyze
    alerts = analyze_trades(trades)
    print(f"Found {len(alerts)} alert-worthy signals")
    
    # Post top 3
    for alert in alerts[:3]:
        message = format_alert(alert)
        success = post_telegram(message)
        print(f"Posted {alert['token']} {alert['side']}: {'✓' if success else '✗'}")


if __name__ == "__main__":
    main()
