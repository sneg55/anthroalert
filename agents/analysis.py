"""Analysis Agent — daily/on-demand deep analysis of multi-day smart-money trends."""

import json
import logging
import subprocess
from datetime import datetime, timezone, timedelta

import anthropic

import config
from agents.db import get_db, init_db

logger = logging.getLogger("anthroalert.analysis")

ANALYSIS_SYSTEM_PROMPT = """\
You are AnthroAlert's Analysis Agent. You receive multi-day smart-money data for BTC perps on Hyperliquid.

Your job:
1. Identify multi-day trends: shifting bias, accumulation/distribution, notable wallet behaviour changes.
2. Compare current positioning vs historical baseline.
3. Flag any divergences or notable patterns.

Respond with valid JSON:
{
  "trend_summary": "<2-4 sentence overview>",
  "bias": "bullish|bearish|neutral",
  "confidence": <float 0-1>,
  "notable_changes": ["<change 1>", "<change 2>", ...],
  "recommendation": "<1 sentence actionable insight>"
}
"""


class AnalysisAgent:
    """Runs deeper analysis on multi-day historical data."""

    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.model = config.MODEL_SONNET
        init_db()

    def run(self, days: int = 7) -> dict:
        """Run multi-day trend analysis. Returns analysis dict."""
        logger.info("Analysis Agent: starting %d-day analysis", days)

        # Gather data from two sources
        historical_db = self._load_historical(days)
        netflow_data = self._fetch_netflow()

        if not historical_db and not netflow_data:
            logger.info("Analysis Agent: insufficient data for analysis")
            return {"bias": "neutral", "confidence": 0.0, "trend_summary": "Insufficient data"}

        return self._analyse(historical_db, netflow_data, days)

    # ── data sources ──────────────────────────────────────────────────
    @staticmethod
    def _load_historical(days: int) -> list[dict]:
        db = get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = db.execute(
            "SELECT fetched_at, payload FROM raw_data WHERE fetched_at >= ? ORDER BY fetched_at",
            (cutoff,),
        ).fetchall()
        results = []
        for ts, payload in rows:
            try:
                results.append({"fetched_at": ts, "data": json.loads(payload)})
            except json.JSONDecodeError:
                continue
        return results

    @staticmethod
    def _fetch_netflow() -> dict | None:
        """Pull smart-money netflow via Nansen CLI for broader context."""
        try:
            proc = subprocess.run(
                config.NANSEN_SMART_MONEY_NETFLOW,
                capture_output=True,
                text=True,
                timeout=60,
                env={**__import__("os").environ, "NANSEN_API_KEY": config.NANSEN_API_KEY},
            )
            if proc.returncode == 0:
                return json.loads(proc.stdout)
        except Exception as exc:
            logger.warning("Analysis netflow fetch failed: %s", exc)
        return None

    # ── Claude analysis ───────────────────────────────────────────────
    def _analyse(self, historical: list[dict], netflow: dict | None, days: int) -> dict:
        data_payload = {
            "historical_positions": historical[:200],  # cap to manage token usage
            "netflow": netflow,
            "days_covered": days,
            "total_data_points": len(historical),
        }

        user_content = (
            f"Analyse the following {days}-day smart-money BTC perp data from Nansen.\n"
            f"Data points: {len(historical)}\n\n"
            f"```json\n{json.dumps(data_payload, indent=2)[:15000]}\n```"
        )

        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=config.ANTHROPIC_MAX_TOKENS,
                temperature=config.ANTHROPIC_TEMPERATURE,
                system=ANALYSIS_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            text = resp.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            result = json.loads(text)
            logger.info("Analysis Agent: bias=%s confidence=%.2f", result.get("bias"), result.get("confidence", 0))

            # Store analysis result
            self._store_analysis(result, days)
            return result

        except json.JSONDecodeError:
            logger.error("Analysis Agent: failed to parse response")
            return {"bias": "neutral", "confidence": 0.0, "trend_summary": "Parse error"}
        except Exception as exc:
            logger.error("Analysis Agent error: %s", exc)
            return {"bias": "neutral", "confidence": 0.0, "trend_summary": str(exc)[:200]}

    @staticmethod
    def _store_analysis(result: dict, days: int) -> None:
        db = get_db()
        db.execute(
            "INSERT INTO analysis_reports (timestamp, days_covered, report) VALUES (?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), days, json.dumps(result)),
        )
        db.commit()
