"""SQLite database helpers for the Alexa → AnyList Flask app."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "shopping_sync.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't already exist."""
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS sync_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT    NOT NULL,
                level     TEXT    NOT NULL,
                message   TEXT    NOT NULL
            );
        """)


# ── Settings ──────────────────────────────────────────────────────────────────

def get_setting(key: str, default: str | None = None) -> str | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )


def get_all_settings() -> dict:
    with _conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


# ── Sync log ──────────────────────────────────────────────────────────────────

def add_log(level: str, message: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO sync_log (timestamp, level, message) VALUES (?, ?, ?)",
            (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), level, message),
        )


def get_recent_logs(limit: int = 100) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, level, message FROM sync_log "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def clear_logs() -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM sync_log")
