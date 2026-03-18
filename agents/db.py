"""SQLite database helper for AnthroAlert."""

import sqlite3
import threading

import config

_local = threading.local()


def get_db() -> sqlite3.Connection:
    """Get a thread-local SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _local.conn = sqlite3.connect(str(config.DB_PATH))
        _local.conn.execute("PRAGMA journal_mode=WAL")
    return _local.conn


def init_db() -> None:
    """Create tables if they don't exist."""
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS raw_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at TEXT NOT NULL,
            payload TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cycle_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            summary TEXT,
            full_result TEXT
        );

        CREATE TABLE IF NOT EXISTS analysis_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            days_covered INTEGER,
            report TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_raw_data_fetched_at ON raw_data(fetched_at);
        CREATE INDEX IF NOT EXISTS idx_cycle_logs_timestamp ON cycle_logs(timestamp);
    """)
    db.commit()
