"""JARVIS — Conversation database (SQLite)."""
from __future__ import annotations

import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_LOGGER = logging.getLogger(__name__)

DB_PATH = Path("/config/jarvis/conversations.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    device_id   TEXT    NOT NULL DEFAULT 'unknown',
    role        TEXT    NOT NULL CHECK(role IN ('user','assistant')),
    content     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_timestamp ON conversations(timestamp);
CREATE INDEX IF NOT EXISTS idx_device    ON conversations(device_id);

CREATE TABLE IF NOT EXISTS sentinel_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    entity_id   TEXT    NOT NULL,
    event_type  TEXT    NOT NULL,
    detail      TEXT
);

CREATE TABLE IF NOT EXISTS activity_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    entity_id   TEXT    NOT NULL DEFAULT '',
    category    TEXT    NOT NULL DEFAULT 'other',
    urgency     TEXT    NOT NULL DEFAULT 'low',
    message     TEXT    NOT NULL DEFAULT '',
    was_spoken  INTEGER NOT NULL DEFAULT 0,
    source      TEXT    NOT NULL DEFAULT 'observer'
);
CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity_log(timestamp);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ── Conversation history ──────────────────────────────────────────────────────

def save_message(role: str, content: str, device_id: str = "unknown") -> None:
    """Persist a single conversation turn."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO conversations (timestamp, device_id, role, content) VALUES (?,?,?,?)",
                (datetime.utcnow().isoformat(), device_id, role, content),
            )
    except Exception as exc:
        _LOGGER.warning("JARVIS DB write error: %s", exc)


def get_recent_messages(
    hours: int = 24,
    device_id: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    """Return recent conversation rows, oldest first."""
    try:
        since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        with _connect() as conn:
            if device_id:
                rows = conn.execute(
                    "SELECT * FROM conversations WHERE timestamp > ? AND device_id = ? "
                    "ORDER BY timestamp ASC LIMIT ?",
                    (since, device_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM conversations WHERE timestamp > ? "
                    "ORDER BY timestamp ASC LIMIT ?",
                    (since, limit),
                ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        _LOGGER.warning("JARVIS DB read error: %s", exc)
        return []


def get_stats() -> dict:
    """Return quick stats for diagnostics."""
    try:
        with _connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            oldest = conn.execute(
                "SELECT MIN(timestamp) FROM conversations"
            ).fetchone()[0]
            return {"total_messages": total, "oldest_entry": oldest}
    except Exception:
        return {}


# ── Sentinel events ───────────────────────────────────────────────────────────

def save_sentinel_event(entity_id: str, event_type: str, detail: str = "") -> None:
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO sentinel_events (timestamp, entity_id, event_type, detail) VALUES (?,?,?,?)",
                (datetime.utcnow().isoformat(), entity_id, event_type, detail),
            )
    except Exception as exc:
        _LOGGER.warning("JARVIS sentinel DB write error: %s", exc)


def purge_old_records(days: int = 30) -> int:
    """Delete entries older than `days`. Returns rows deleted."""
    try:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM conversations WHERE timestamp < ?", (cutoff,)
            )
            conn.execute(
                "DELETE FROM sentinel_events WHERE timestamp < ?", (cutoff,)
            )
            conn.execute(
                "DELETE FROM activity_log WHERE timestamp < ?", (cutoff,)
            )
            return cur.rowcount
    except Exception as exc:
        _LOGGER.warning("JARVIS DB purge error: %s", exc)
        return 0


# ── Activity log (v5.4.8) ────────────────────────────────────────────────────

def save_activity(
    *,
    entity_id: str = "",
    category: str = "other",
    urgency: str = "low",
    message: str = "",
    was_spoken: bool = False,
    source: str = "observer",
) -> None:
    """Persist an activity log entry (observer event, sentinel alert, etc.)."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO activity_log "
                "(timestamp, entity_id, category, urgency, message, was_spoken, source) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    datetime.utcnow().isoformat(),
                    entity_id,
                    category,
                    urgency,
                    message,
                    1 if was_spoken else 0,
                    source,
                ),
            )
    except Exception as exc:
        _LOGGER.warning("JARVIS activity log write error: %s", exc)


def get_recent_activity(
    hours: int = 24,
    limit: int = 50,
) -> list[dict]:
    """Return recent activity log rows, newest first."""
    try:
        since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        with _connect() as conn:
            rows = conn.execute(
                "SELECT * FROM activity_log WHERE timestamp > ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        _LOGGER.warning("JARVIS activity log read error: %s", exc)
        return []


def get_activity_count_today() -> int:
    """Count of spoken announcements today."""
    try:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0).isoformat()
        with _connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM activity_log WHERE timestamp > ? AND was_spoken = 1",
                (today,),
            ).fetchone()
            return row[0] if row else 0
    except Exception:
        return 0
