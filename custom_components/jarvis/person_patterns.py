"""
JARVIS — per-person routine store (v6.85.0).

The dedicated home for the person_patterns table: the per-household-member
routines the pattern analyzer detects ("Sam usually starts the coffee around
06:40"). The table has existed since v6.41 and been written by the analyzer and
read by the Memory panel; this consolidates that scattered logic behind one
clean API so anticipation and any future consumer read routines from one place.

Every function takes an explicit db_path (default the shared patterns.db), is
self-sufficient (ensures the schema on write), and never raises — a read returns
[] and a write returns False on error. Person ids are normalized via identity so
the store key matches everywhere ('Sam' -> 'sam').
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

_LOGGER = logging.getLogger(__name__)

DB_PATH = "/config/jarvis/patterns.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS person_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT NOT NULL,
    pattern_type TEXT NOT NULL,
    description TEXT NOT NULL,
    data TEXT DEFAULT '{}',
    confidence REAL DEFAULT 0.0,
    last_seen TEXT,
    occurrences INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_pp_person ON person_patterns(person);
"""


def _normalize(person: str) -> str:
    try:
        from . import identity
        return identity.normalize(person)
    except Exception:
        return str(person or "")


def ensure_schema(db_path: str = DB_PATH) -> None:
    """Create the person_patterns table + index if missing. Idempotent.
    (cognitive_core also creates it at init; this keeps the module standalone.)"""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(_SCHEMA)
    except Exception as exc:
        _LOGGER.debug("person_patterns ensure_schema failed: %s", exc)


def store(person: str, pattern_type: str, description: str, *,
          data: Optional[dict] = None, confidence: float = 0.0,
          occurrences: int = 1, db_path: str = DB_PATH) -> bool:
    """Upsert a person routine on (person, pattern_type, description). person is
    normalized; re-analysis refreshes in place rather than duplicating. Returns
    True on success. Never raises."""
    if not person:
        return False
    person = _normalize(person)
    try:
        ensure_schema(db_path)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT id FROM person_patterns "
                "WHERE person = ? AND pattern_type = ? AND description = ?",
                (person, pattern_type, description),
            ).fetchone()
            now_iso = datetime.now().isoformat()
            payload = json.dumps(data or {})
            if row:
                conn.execute(
                    "UPDATE person_patterns SET confidence = ?, occurrences = ?, "
                    "last_seen = ?, data = ? WHERE id = ?",
                    (confidence, occurrences, now_iso, payload, row[0]),
                )
            else:
                conn.execute(
                    "INSERT INTO person_patterns "
                    "(person, pattern_type, description, data, confidence, "
                    "last_seen, occurrences) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (person, pattern_type, description, payload, confidence,
                     now_iso, occurrences),
                )
        return True
    except Exception as exc:
        _LOGGER.debug("person_patterns store failed: %s", exc)
        return False


def read(person: Optional[str] = None, db_path: str = DB_PATH) -> list[dict]:
    """Read stored routines, optionally for one (normalized) person, ordered by
    confidence. Returns a list of dicts (column-keyed). Never raises."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            if person:
                rows = conn.execute(
                    "SELECT * FROM person_patterns WHERE person = ? "
                    "ORDER BY confidence DESC", (_normalize(person),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM person_patterns ORDER BY person, confidence DESC"
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        _LOGGER.debug("person_patterns read failed: %s", exc)
        return []
