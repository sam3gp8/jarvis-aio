"""JARVIS — Conversation database (SQLite)."""
from __future__ import annotations

import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .paths import config_path
from .sqlite_utils import ClosingConnection

_LOGGER = logging.getLogger(__name__)

DB_PATH = config_path("jarvis", "conversations.db")

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


_last_error: Optional[str] = None   # last connect/schema failure, for diagnostics


# How long a contended connection waits for a lock before giving up. The
# conversation store is written by the observer/agent while diagnostics and the
# activity feed read it, so without this a concurrent access raised "database is
# locked" immediately (see database.py read/connect errors).
_BUSY_TIMEOUT_S = 15.0


def _connect() -> sqlite3.Connection:
    global _last_error
    conn: sqlite3.Connection | None = None
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        # timeout → wait for a lock instead of raising "database is locked" at
        # once; WAL → a reader and a writer can hold the DB at the same time
        # (the recorder-style concurrency HA itself uses). busy_timeout is the
        # same wait expressed as a pragma, for the WAL checkpointer too.
        conn = sqlite3.connect(
            str(DB_PATH), timeout=_BUSY_TIMEOUT_S, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            # A read-only FS or an exclusive lock can refuse the switch; the
            # busy_timeout below still prevents the immediate-lock failures.
            pass
        conn.execute(f"PRAGMA busy_timeout={int(_BUSY_TIMEOUT_S * 1000)}")
        conn.executescript(SCHEMA)
        conn.commit()
        _last_error = None
        return conn
    except Exception as exc:
        # A schema/migration failure is NOT swallowed silently — it's logged and
        # retained so diagnostics can report the store as degraded. Callers still
        # handle the raised error for the individual operation.
        if conn is not None:
            conn.close()
        _last_error = f"{type(exc).__name__}: {exc}"
        _LOGGER.error("conversation DB connect/schema failed: %s", exc)
        raise


def health() -> dict:
    """Actively probe the conversation store: connect, apply the schema, and run
    a trivial read. Returns ``{'ok': bool, 'error': str}``. Diagnostics uses this
    so a failed schema/migration surfaces as degraded instead of silent no-ops."""
    try:
        conn = _connect()
        try:
            conn.execute("SELECT 1 FROM conversations LIMIT 1")
        finally:
            conn.close()
        return {"ok": True, "error": ""}
    except Exception as exc:
        return {"ok": False, "error": _last_error or f"{type(exc).__name__}: {exc}"}


# ── Conversation history ──────────────────────────────────────────────────────

def save_message(role: str, content: str, device_id: str = "unknown") -> None:
    """Persist a single conversation turn."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO conversations (timestamp, device_id, role, content) VALUES (?,?,?,?)",
                (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), device_id, role, content),
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
        since = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)).isoformat()
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
                (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), entity_id, event_type, detail),
            )
    except Exception as exc:
        _LOGGER.warning("JARVIS sentinel DB write error: %s", exc)


def purge_old_records(days: int = 30) -> int:
    """Delete conversation / sentinel / activity rows older than `days`, then
    VACUUM to reclaim the freed disk (a plain DELETE leaves the file the same
    size). `days` <= 0 is a no-op (keep forever). Returns total rows deleted."""
    try:
        if int(days) <= 0:
            return 0                              # retain everything
    except (TypeError, ValueError):
        return 0
    try:
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()
        deleted = 0
        with _connect() as conn:
            for tbl in ("conversations", "sentinel_events", "activity_log"):
                cur = conn.execute(f"DELETE FROM {tbl} WHERE timestamp < ?", (cutoff,))
                deleted += cur.rowcount or 0
        if deleted:
            try:
                with sqlite3.connect(str(DB_PATH), isolation_level=None,
                                     factory=ClosingConnection) as vac:
                    vac.execute("VACUUM")
            except Exception:
                pass
        return deleted
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
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
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
        since = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)).isoformat()
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
        today = datetime.now(timezone.utc).replace(tzinfo=None).replace(hour=0, minute=0, second=0).isoformat()
        with _connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM activity_log WHERE timestamp > ? AND was_spoken = 1",
                (today,),
            ).fetchone()
            return row[0] if row else 0
    except Exception:
        return 0
