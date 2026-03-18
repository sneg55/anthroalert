"""Detect Agent — scores fresh data for alert-worthy signals using Claude Sonnet."""

import json
import logging
from datetime import datetime, timezone, timedelta

import anthropic

import config
from agents.db import get_db

logger = logging.getLogger("anthroalert.detect")

DETECT_SYSTEM_PROMPT = """\
You are AnthroAlert's Detect Agent — a smart-money signal scorer for BTC perpetuals on Hyperliquid.

You will receive recent Nansen CLI data (positions, trades, netflows).
Your job:
1. Identify noteworthy patterns: large notional, clustered wallets, directional bias, unusual leverage.
2. Score overall significance from 0.0 to 1.0.
3. If score >= {min_confidence}, produce an alert summary with key metrics.
4. If nothing significant, return early with score 0.

Respond ONLY with valid JSON:
{{
  "score": <float 0-1>,
  "alert_worthy": <bool>,
  "summary": "<1-3 sentence description of what happened>",
  "key_metrics": {{
    "dominant_side": "long|short|mixed",
    "total_notional_usd": <number>,
    "wallet_count": <int>,
    "largest_position_usd": <number>,
    "notable_wallets": ["<addr_short>", ...]
  }}
}}
""".format(min_confidence=config.MIN_CONFIDENCE)


class DetectAgent:
    """Analyses fresh data and returns signal assessments."""

    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.model = config.MODEL_SONNET

    def run(self) -> dict:
        """Evaluate recent data. Returns parsed signal dict or {"alert_worthy": False}."""
        fresh_data = self._load_fresh_data()
        if not fresh_data:
            logger.info("Detect Agent: no fresh data — early exit")
            return {"alert_worthy": False, "score": 0.0, "summary": "No fresh data"}

        logger.info("Detect Agent: analysing %d rows", len(fresh_data))
        return self._evaluate(fresh_data)

    # ── data loading ──────────────────────────────────────────────────
    @staticmethod
    def _load_fresh_data() -> list[dict]:
        db = get_db()
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=config.MAX_DATA_AGE_SECONDS)
        ).isoformat()
        rows = db.execute(
            "SELECT payload FROM raw_data WHERE fetched_at >= ? ORDER BY fetched_at DESC",
            (cutoff,),
        ).fetchall()
        results = []
        for (payload,) in rows:
            try:
                results.append(json.loads(payload))
            except json.JSONDecodeError:
                continue
        return results

    # ── Claude evaluation ─────────────────────────────────────────────
    def _evaluate(self, data: list[dict]) -> dict:
        user_content = (
            "Here is the latest Nansen smart-money perp data for BTC on Hyperliquid.\n"
            "Apply the detection thresholds:\n"
            f"- MIN_NOTIONAL_USD: {config.MIN_NOTIONAL_USD}\n"
            f"- MIN_CLUSTER_SIZE: {config.MIN_CLUSTER_SIZE}\n"
            f"- MIN_CONFIDENCE: {config.MIN_CONFIDENCE}\n\n"
            "Data:\n"
            f"```json\n{json.dumps(data, indent=2)[:12000]}\n```"
        )

        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=config.ANTHROPIC_MAX_TOKENS,
                temperature=config.ANTHROPIC_TEMPERATURE,
                system=DETECT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            text = resp.content[0].text.strip()

            # Strip markdown fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            result = json.loads(text)
            logger.info("Detect Agent: score=%.2f alert_worthy=%s", result.get("score", 0), result.get("alert_worthy"))
            return result

        except json.JSONDecodeError:
            logger.error("Detect Agent: failed to parse Claude response as JSON")
            return {"alert_worthy": False, "score": 0.0, "summary": "Parse error"}
        except anthropic.RateLimitError:
            logger.warning("Detect Agent: Anthropic rate limited")
            return {"alert_worthy": False, "score": 0.0, "summary": "Rate limited"}
        except Exception as exc:
            logger.error("Detect Agent error: %s", exc)
            return {"alert_worthy": False, "score": 0.0, "summary": str(exc)[:200]}
