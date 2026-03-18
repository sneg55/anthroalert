"""Fetch Agent — pulls smart-money perp data via Nansen CLI (subprocess)."""

import json
import logging
import subprocess
import time
from datetime import datetime, timezone

import anthropic

import config
from agents.db import get_db, init_db

logger = logging.getLogger("anthroalert.fetch")


class FetchAgent:
    """Executes Nansen CLI commands and stores raw results in SQLite."""

    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.model = config.MODEL_HAIKU
        init_db()

    # ── public ────────────────────────────────────────────────────────
    def run(self) -> dict:
        """Run a full fetch cycle. Returns {"ok": bool, "rows": int, "error": str|None}."""
        logger.info("Fetch Agent: starting cycle")
        results: list[dict] = []
        errors: list[str] = []

        for label, cmd in self._commands():
            data, err = self._exec_nansen(cmd)
            if err:
                errors.append(f"{label}: {err}")
                continue
            if data:
                results.extend(data if isinstance(data, list) else [data])

        if not results and errors:
            return {"ok": False, "rows": 0, "error": "; ".join(errors)}

        stored = self._store(results)
        logger.info("Fetch Agent: stored %d rows", stored)
        return {"ok": True, "rows": stored, "error": None}

    # ── nansen CLI execution ──────────────────────────────────────────
    def _commands(self) -> list[tuple[str, list[str]]]:
        return [
            ("smart-money-perp-trades", config.NANSEN_SMART_MONEY_PERP_TRADES),
            ("token-perp-positions", config.NANSEN_TOKEN_PERP_POSITIONS),
            ("token-perp-trades", config.NANSEN_TOKEN_PERP_TRADES),
        ]

    def _exec_nansen(self, cmd: list[str], attempt: int = 1) -> tuple[list | None, str | None]:
        """Execute a Nansen CLI command. Returns (parsed_json, error_string)."""
        try:
            logger.debug("Running: %s", " ".join(cmd))
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                env={**__import__("os").environ, "NANSEN_API_KEY": config.NANSEN_API_KEY},
            )

            if proc.returncode != 0:
                err_text = proc.stderr.strip() or proc.stdout.strip()
                error_code = self._extract_error_code(err_text)

                if error_code in config.NANSEN_FATAL_ERRORS:
                    logger.error("Fatal Nansen error: %s", error_code)
                    return None, f"FATAL:{error_code}"

                if error_code in config.NANSEN_RETRYABLE_ERRORS and attempt <= config.MAX_RETRIES:
                    wait = config.RETRY_BACKOFF_BASE ** attempt
                    logger.warning("RATE_LIMITED — retrying in %ds (attempt %d)", wait, attempt)
                    time.sleep(wait)
                    return self._exec_nansen(cmd, attempt + 1)

                return None, err_text[:300]

            return json.loads(proc.stdout), None

        except subprocess.TimeoutExpired:
            return None, "TIMEOUT"
        except json.JSONDecodeError as exc:
            return None, f"JSON_PARSE_ERROR: {exc}"
        except Exception as exc:
            return None, str(exc)[:300]

    @staticmethod
    def _extract_error_code(text: str) -> str | None:
        for code in ("RATE_LIMITED", "CREDITS_EXHAUSTED", "UNAUTHORIZED"):
            if code in text:
                return code
        return None

    # ── persistence ───────────────────────────────────────────────────
    @staticmethod
    def _store(rows: list[dict]) -> int:
        db = get_db()
        ts = datetime.now(timezone.utc).isoformat()
        stored = 0
        for row in rows:
            db.execute(
                "INSERT INTO raw_data (fetched_at, payload) VALUES (?, ?)",
                (ts, json.dumps(row)),
            )
            stored += 1
        db.commit()
        return stored
