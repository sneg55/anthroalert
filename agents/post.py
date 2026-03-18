"""Post Agent — formats and sends alerts to Discord #nansen via openclaw CLI."""

import json
import logging
import subprocess
from datetime import datetime, timezone

import anthropic

import config

logger = logging.getLogger("anthroalert.post")

FORMAT_SYSTEM_PROMPT = """\
You are AnthroAlert's Post Agent. Format a clean Discord alert message from the signal data.

Rules:
- Use clear emoji markers: 🔴 for shorts, 🟢 for longs, ⚠️ for mixed.
- Include: direction, total notional, wallet count, largest position, notable wallets.
- Keep it under 400 characters for readability.
- Add "📡 AnthroAlert • Nansen Smart Money" footer.
- Output ONLY the message text, nothing else.
"""


class PostAgent:
    """Formats signal data and posts to Discord via openclaw CLI."""

    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.model = config.MODEL_HAIKU

    def run(self, signal: dict) -> dict:
        """Format and post an alert. Returns {"ok": bool, "error": str|None}."""
        if not signal.get("alert_worthy"):
            return {"ok": True, "error": None, "skipped": True}

        message = self._format_message(signal)
        if not message:
            return {"ok": False, "error": "Failed to format message"}

        return self._post_to_telegram(message)

    # ── message formatting via Claude ─────────────────────────────────
    def _format_message(self, signal: dict) -> str | None:
        user_content = (
            "Format this signal into a Discord alert message:\n"
            f"```json\n{json.dumps(signal, indent=2)}\n```"
        )
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=512,
                temperature=0.3,
                system=FORMAT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            return resp.content[0].text.strip()
        except Exception as exc:
            logger.error("Post Agent format error: %s", exc)
            # Fallback: plain text alert
            return self._fallback_format(signal)

    @staticmethod
    def _fallback_format(signal: dict) -> str:
        metrics = signal.get("key_metrics", {})
        side = metrics.get("dominant_side", "unknown")
        emoji = "🟢" if side == "long" else "🔴" if side == "short" else "⚠️"
        notional = metrics.get("total_notional_usd", 0)
        wallets = metrics.get("wallet_count", 0)
        summary = signal.get("summary", "Signal detected")
        ts = datetime.now(timezone.utc).strftime("%H:%M UTC")

        return (
            f"{emoji} **BTC Perp Alert** ({ts})\n"
            f"{summary}\n"
            f"💰 ${notional:,.0f} | 👛 {wallets} wallets | Direction: {side}\n"
            f"📡 AnthroAlert • Nansen Smart Money"
        )

    # ── Telegram delivery via Bot API ───────────────────────────────────
    @staticmethod
    def _post_to_telegram(message: str) -> dict:
        import requests
        
        bot_token = config.TELEGRAM_BOT_TOKEN
        chat_id = config.TELEGRAM_CHAT_ID
        
        if not bot_token:
            logger.error("TELEGRAM_BOT_TOKEN not configured")
            return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set"}
        
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            logger.info("Posting alert to Telegram chat %s", chat_id)
            resp = requests.post(url, json=payload, timeout=30)
            data = resp.json()
            if not data.get("ok"):
                err = data.get("description", "Unknown error")[:300]
                logger.error("Telegram post failed: %s", err)
                return {"ok": False, "error": err}
            logger.info("Alert posted successfully")
            return {"ok": True, "error": None}
        except requests.Timeout:
            return {"ok": False, "error": "TIMEOUT posting to Telegram"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}
