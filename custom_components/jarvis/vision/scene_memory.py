"""
JARVIS scene memory (v8.3.0) — persistent object/scene recall across time.

``spatial.py`` fuses *live* presence signals; it has no memory. Individual camera
analyses (camera.async_analyze_camera) produce a rich natural-language scene
description each time, but they were thrown away after the announcement decision.
This module keeps them: one row per analysed frame — the camera, the time, the
description, and the content-word "objects" extracted from it — so JARVIS can
answer questions that need *history*, not just the current frame:

    "Where did I last see my keys?"        → where_last_seen("keys")
    "What's different in the garage?"       → changed_since(camera, yesterday)
    "What's on the porch right now?"        → inventory(camera)

It is deliberately cheap and self-contained: pure SQLite (sibling of the other
jarvis DBs), no vision calls of its own (it consumes descriptions the camera
pipeline already generates), stdlib only, and every entry point takes a
``db_path`` resolved at call time so tests run fully isolated. Bounded: old rows
are trimmed per camera so the store can't grow without limit.

All DB functions are SYNC — call them via hass.async_add_executor_job(...).
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import Optional

try:
    from ..paths import config_path_str
    DB_PATH = config_path_str("jarvis", "scene_memory.db")
except Exception:  # pragma: no cover - only if paths helper is unavailable
    DB_PATH = "/config/jarvis/scene_memory.db"

_LOGGER = logging.getLogger(__name__)

MAX_ROWS_PER_CAMERA = 500     # trim history so the store stays bounded

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "to", "in", "on", "at",
    "for", "and", "or", "with", "no", "not", "there", "here", "this", "that",
    "it", "its", "as", "by", "from", "into", "over", "near", "up", "down",
    "appears", "appear", "seems", "seem", "shows", "shown", "visible", "seen",
    "image", "frame", "camera", "scene", "view", "picture", "photo", "shot",
    "looks", "look", "some", "any", "left", "right", "front", "back", "side",
}


def _resolve(db_path: Optional[str]) -> str:
    return db_path or DB_PATH


def _connect(db_path: str) -> Optional[sqlite3.Connection]:
    try:
        import os
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        # Shared connection factory (sqlite_utils.ClosingConnection) — guarantees
        # the handle closes even if a caller forgets, matching the rest of the
        # codebase's SQLite stores. Tolerant of an import context where the
        # package parent isn't available (falls back to a plain connection; this
        # module already closes handles explicitly in every finally).
        try:
            from ..sqlite_utils import ClosingConnection
            conn = sqlite3.connect(db_path, timeout=10, factory=ClosingConnection)
        except Exception:
            conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        return conn
    except Exception as exc:
        _LOGGER.warning("scene_memory: connect failed: %s", exc)
        return None


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sightings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            camera      TEXT NOT NULL,
            area        TEXT,
            ts          REAL NOT NULL,
            description TEXT NOT NULL,
            objects     TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sight_camera ON sightings(camera, ts)")
    conn.commit()


def _objects_from_text(text: str) -> list[str]:
    """Content-word tokens from a scene description — a cheap, deterministic stand-in
    for a structured object list. Not linguistics; just enough to diff and search."""
    toks = [t for t in re.findall(r"[a-z][a-z0-9\-]{2,}", (text or "").lower())
            if t not in _STOPWORDS]
    # de-dup, preserve order
    seen: set = set()
    out: list[str] = []
    for t in toks:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _row_to_sighting(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "camera": row["camera"],
        "area": row["area"],
        "ts": row["ts"],
        "description": row["description"],
        "objects": [o for o in (row["objects"] or "").split("\n") if o],
    }


# ── write ──────────────────────────────────────────────────────────────────

def record_scene(
    camera: str,
    description: str,
    *,
    area: Optional[str] = None,
    ts: Optional[float] = None,
    objects: Optional[list] = None,
    db_path: Optional[str] = None,
) -> Optional[dict]:
    """Persist one analysed frame. ``objects`` may be supplied by a structured
    detector; otherwise they are extracted from the description. Trims the
    camera's history to MAX_ROWS_PER_CAMERA. Returns the stored row. SYNC."""
    camera = (camera or "").strip()
    description = (description or "").strip()
    if not camera or not description:
        return None
    ts = ts if ts is not None else time.time()
    objs = objects if objects is not None else _objects_from_text(description)
    objs = [str(o).strip().lower() for o in objs if str(o).strip()]
    db = _resolve(db_path)
    conn = _connect(db)
    if conn is None:
        return None
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO sightings (camera, area, ts, description, objects) "
                "VALUES (?, ?, ?, ?, ?)",
                (camera, area, ts, description, "\n".join(objs)),
            )
            rid = cur.lastrowid
            # bound history for this camera
            conn.execute(
                "DELETE FROM sightings WHERE camera = ? AND id NOT IN "
                "(SELECT id FROM sightings WHERE camera = ? ORDER BY ts DESC LIMIT ?)",
                (camera, camera, MAX_ROWS_PER_CAMERA),
            )
            row = conn.execute("SELECT * FROM sightings WHERE id = ?", (rid,)).fetchone()
        return _row_to_sighting(row) if row else None
    except Exception as exc:
        _LOGGER.warning("scene_memory: record_scene failed: %s", exc)
        return None
    finally:
        conn.close()


# ── read ───────────────────────────────────────────────────────────────────

def where_last_seen(term: str, *, db_path: Optional[str] = None) -> Optional[dict]:
    """Most recent sighting whose objects or description mention ``term``.
    Returns {camera, area, ts, description, objects} or None. SYNC."""
    term = (term or "").strip().lower()
    if not term:
        return None
    db = _resolve(db_path)
    conn = _connect(db)
    if conn is None:
        return None
    try:
        like = f"%{term}%"
        row = conn.execute(
            "SELECT * FROM sightings WHERE lower(objects) LIKE ? OR lower(description) LIKE ? "
            "ORDER BY ts DESC LIMIT 1",
            (like, like),
        ).fetchone()
        return _row_to_sighting(row) if row else None
    except Exception as exc:
        _LOGGER.warning("scene_memory: where_last_seen failed: %s", exc)
        return None
    finally:
        conn.close()


def inventory(camera: str, *, db_path: Optional[str] = None) -> dict:
    """The most recent scene for one camera: its objects + description. SYNC."""
    camera = (camera or "").strip()
    db = _resolve(db_path)
    conn = _connect(db)
    if conn is None:
        return {}
    try:
        row = conn.execute(
            "SELECT * FROM sightings WHERE camera = ? ORDER BY ts DESC LIMIT 1",
            (camera,),
        ).fetchone()
        return _row_to_sighting(row) if row else {}
    except Exception as exc:
        _LOGGER.warning("scene_memory: inventory failed: %s", exc)
        return {}
    finally:
        conn.close()


def changed_since(camera: str, since: float, *, db_path: Optional[str] = None) -> dict:
    """Compare the latest scene for ``camera`` against the last one recorded at or
    before ``since``. Returns {added, removed, latest_ts, baseline_ts}: objects
    present now but not then, and vice versa. Empty lists when there's nothing to
    compare. SYNC."""
    camera = (camera or "").strip()
    out = {"added": [], "removed": [], "latest_ts": None, "baseline_ts": None}
    db = _resolve(db_path)
    conn = _connect(db)
    if conn is None:
        return out
    try:
        latest = conn.execute(
            "SELECT * FROM sightings WHERE camera = ? ORDER BY ts DESC LIMIT 1",
            (camera,),
        ).fetchone()
        baseline = conn.execute(
            "SELECT * FROM sightings WHERE camera = ? AND ts <= ? ORDER BY ts DESC LIMIT 1",
            (camera, since),
        ).fetchone()
        if not latest or not baseline or latest["id"] == baseline["id"]:
            if latest:
                out["latest_ts"] = latest["ts"]
            return out
        now_objs = set(_row_to_sighting(latest)["objects"])
        then_objs = set(_row_to_sighting(baseline)["objects"])
        out["added"] = sorted(now_objs - then_objs)
        out["removed"] = sorted(then_objs - now_objs)
        out["latest_ts"] = latest["ts"]
        out["baseline_ts"] = baseline["ts"]
        return out
    except Exception as exc:
        _LOGGER.warning("scene_memory: changed_since failed: %s", exc)
        return out
    finally:
        conn.close()


def recent(camera: Optional[str] = None, *, limit: int = 20,
           db_path: Optional[str] = None) -> list[dict]:
    """Most recent sightings (optionally for one camera), newest first. SYNC."""
    db = _resolve(db_path)
    conn = _connect(db)
    if conn is None:
        return []
    try:
        if camera:
            rows = conn.execute(
                "SELECT * FROM sightings WHERE camera = ? ORDER BY ts DESC LIMIT ?",
                (camera.strip(), max(1, limit))).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sightings ORDER BY ts DESC LIMIT ?",
                (max(1, limit),)).fetchall()
        return [_row_to_sighting(r) for r in rows]
    except Exception as exc:
        _LOGGER.warning("scene_memory: recent failed: %s", exc)
        return []
    finally:
        conn.close()
