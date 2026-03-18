#!/usr/bin/env python3
"""AnthroAlert — entry point. Runs the Coordinator on a loop or single shot."""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

import config  # noqa: E402 — must load after dotenv
from agents.coordinator import CoordinatorAgent  # noqa: E402
from agents.analysis import AnalysisAgent  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_DIR / "anthroalert.log"),
    ],
)
logger = logging.getLogger("anthroalert")


def run_once() -> dict:
    """Run a single Fetch → Detect → Post cycle."""
    coordinator = CoordinatorAgent()
    return coordinator.run_cycle()


def run_loop() -> None:
    """Run the coordinator in a continuous loop (every CYCLE_INTERVAL_SECONDS)."""
    coordinator = CoordinatorAgent()
    logger.info("AnthroAlert starting loop — interval %ds", config.CYCLE_INTERVAL_SECONDS)

    while True:
        try:
            result = coordinator.run_cycle()
            logger.info("Cycle complete: %s", result.get("detect", {}).get("score", "N/A"))
        except KeyboardInterrupt:
            logger.info("Shutting down (keyboard interrupt)")
            break
        except Exception as exc:
            logger.error("Cycle error: %s", exc)

        logger.info("Next cycle in %ds", config.CYCLE_INTERVAL_SECONDS)
        try:
            time.sleep(config.CYCLE_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            logger.info("Shutting down (keyboard interrupt)")
            break


def run_analysis(days: int = 7) -> dict:
    """Run the Analysis Agent for multi-day trend report."""
    agent = AnalysisAgent()
    return agent.run(days=days)


def main() -> None:
    parser = argparse.ArgumentParser(description="AnthroAlert — Smart Money Alert System")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--analysis", action="store_true", help="Run analysis agent")
    parser.add_argument("--days", type=int, default=7, help="Days for analysis (default: 7)")
    args = parser.parse_args()

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    if args.analysis:
        result = run_analysis(days=args.days)
        print(f"\nAnalysis result:\n{result}")
    elif args.once:
        result = run_once()
        print(f"\nCycle result:\n{result}")
    else:
        run_loop()


if __name__ == "__main__":
    main()
