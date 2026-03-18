"""Coordinator Agent — orchestrates Fetch → Detect → Post pipeline each cycle."""

import json
import logging
from datetime import datetime, timezone

import anthropic

import config
from agents.fetch import FetchAgent
from agents.detect import DetectAgent
from agents.post import PostAgent
from agents.db import get_db, init_db

logger = logging.getLogger("anthroalert.coordinator")

COORDINATOR_SYSTEM_PROMPT = """\
You are AnthroAlert's Coordinator Agent. You receive a JSON summary of this cycle's results.
Produce a brief 1-2 sentence log summary. Include: fetch status, detection score, whether alert was posted.
Output ONLY the summary text.
"""


class CoordinatorAgent:
    """Runs one full Fetch → Detect → Post cycle and logs the outcome."""

    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.model = config.MODEL_HAIKU
        self.fetch_agent = FetchAgent()
        self.detect_agent = DetectAgent()
        self.post_agent = PostAgent()
        init_db()

    def run_cycle(self) -> dict:
        """Execute one full pipeline cycle. Returns summary dict."""
        cycle_start = datetime.now(timezone.utc).isoformat()
        result = {"cycle_start": cycle_start, "fetch": None, "detect": None, "post": None, "error": None}

        # Step 1: Fetch
        try:
            fetch_result = self.fetch_agent.run()
            result["fetch"] = fetch_result
            if not fetch_result["ok"]:
                error = fetch_result.get("error", "")
                if any(f"FATAL:{code}" in str(error) for code in config.NANSEN_FATAL_ERRORS):
                    result["error"] = f"Fatal fetch error: {error}"
                    self._log_cycle(result)
                    return result
        except Exception as exc:
            result["error"] = f"Fetch exception: {exc}"
            self._log_cycle(result)
            return result

        # Step 2: Detect
        try:
            detect_result = self.detect_agent.run()
            result["detect"] = detect_result
        except Exception as exc:
            result["error"] = f"Detect exception: {exc}"
            self._log_cycle(result)
            return result

        # Step 3: Post (only if alert-worthy)
        if detect_result.get("alert_worthy"):
            try:
                post_result = self.post_agent.run(detect_result)
                result["post"] = post_result
            except Exception as exc:
                result["error"] = f"Post exception: {exc}"
        else:
            result["post"] = {"ok": True, "skipped": True}

        self._log_cycle(result)
        return result

    # ── logging ───────────────────────────────────────────────────────
    def _log_cycle(self, result: dict) -> None:
        summary = self._generate_summary(result)
        logger.info("Cycle summary: %s", summary)

        db = get_db()
        db.execute(
            "INSERT INTO cycle_logs (timestamp, summary, full_result) VALUES (?, ?, ?)",
            (
                result["cycle_start"],
                summary,
                json.dumps(result, default=str),
            ),
        )
        db.commit()

    def _generate_summary(self, result: dict) -> str:
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=256,
                temperature=0.1,
                system=COORDINATOR_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(result, default=str)}],
            )
            return resp.content[0].text.strip()
        except Exception as exc:
            # Fallback plain summary
            fetch_ok = result.get("fetch", {}).get("ok", False)
            score = result.get("detect", {}).get("score", 0)
            posted = not result.get("post", {}).get("skipped", True)
            return f"Fetch={'OK' if fetch_ok else 'FAIL'} | Score={score:.2f} | Posted={posted} | Error={result.get('error')}"
