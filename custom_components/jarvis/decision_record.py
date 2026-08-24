"""JARVIS Decision Record (v7.32.0) — one immutable record per proactive decision.

Every proactive decision JARVIS makes (an anticipation alert, an intrusion escalation,
an automation suggestion) is written here as a single row that separates:

  * observation    — the raw facts JARVIS saw (entities, times, presence), no judgement
  * interpretation — JARVIS's *reading* of those facts (what it concluded)
  * evidence       — the supporting signals it leaned on
  * decision + reason — what it chose to do and why
  * model/tokens/latency — the LLM call metadata, when a model was involved (null for
    the deterministic rule-based predictors)
  * confidence     — how sure it was

The row is IMMUTABLE except for a single nullable ``outcome`` field, which the
outcome-capture layer sets later — exactly once — from real signals (a dismissal, a
false alarm). This is the substrate for later evaluation: was the decision actually
right? Keeping observation and interpretation separate is what makes replay and
after-the-fact scoring possible, so it is captured from day one.

The module is deliberately dependency-light (SQLite + stdlib only) and every entry
point takes ``db_path`` resolved at call time, so tests run fully isolated.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

_DEFAULT_DB = "/config/jarvis/decisions.db"

# Recognised outcome verdicts (set by the outcome-capture layer in a later phase).
OUTCOME_GOOD = "good"            # the decision was useful / acted upon
OUTCOME_UNNECESSARY = "unnecessary"  # dismissed as not needed (not wrong, just noise)
OUTCOME_WRONG = "wrong"          # false alarm / incorrect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_records (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             REAL NOT NULL,
    kind           TEXT NOT NULL,
    observation    TEXT NOT NULL DEFAULT '{}',
    interpretation TEXT NOT NULL DEFAULT '{}',
    evidence       TEXT NOT NULL DEFAULT '{}',
    decision       TEXT NOT NULL DEFAULT '',
    reason         TEXT NOT NULL DEFAULT '',
    model          TEXT,
    tokens         INTEGER,
    latency_ms     INTEGER,
    confidence     REAL,
    outcome        TEXT,
    outcome_ts     REAL,
    outcome_source TEXT
);
CREATE INDEX IF NOT EXISTS idx_dr_ts      ON decision_records (ts);
CREATE INDEX IF NOT EXISTS idx_dr_kind    ON decision_records (kind);
CREATE INDEX IF NOT EXISTS idx_dr_outcome ON decision_records (outcome);
"""


def _resolve(db_path: Optional[str]) -> str:
    return db_path or _DEFAULT_DB


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)   # idempotent; keeps every entry point self-contained
    return conn


def _js(v) -> str:
    if v is None:
        return "{}"
    try:
        return json.dumps(v, default=str, sort_keys=True)
    except Exception:
        return "{}"


def _int_or_none(v):
    try:
        return int(v) if v is not None else None
    except Exception:
        return None


def _float_or_none(v):
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for k in ("observation", "interpretation", "evidence"):
        raw = d.get(k)
        try:
            d[k] = json.loads(raw) if raw else {}
        except Exception:
            d[k] = {}
    return d


def ensure_schema(db_path: Optional[str] = None) -> None:
    """Create the table + indexes if absent. Optional — every call self-heals — but
    calling it once at setup keeps the first write cheap."""
    try:
        _connect(_resolve(db_path)).close()
    except Exception:
        pass


def record(
    kind: str,
    observation=None,
    interpretation=None,
    evidence=None,
    decision: str = "",
    reason: str = "",
    model: Optional[str] = None,
    tokens=None,
    latency_ms=None,
    confidence=None,
    ts: Optional[float] = None,
    db_path: Optional[str] = None,
) -> Optional[int]:
    """Insert one immutable decision record. Returns its id, or None on failure.

    Best-effort by design: a logging failure must never break the decision that is
    being logged, so all errors are swallowed and None is returned.
    """
    db = _resolve(db_path)
    try:
        conn = _connect(db)
    except Exception:
        return None
    try:
        cur = conn.execute(
            "INSERT INTO decision_records "
            "(ts, kind, observation, interpretation, evidence, decision, reason, "
            " model, tokens, latency_ms, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                float(ts) if ts is not None else time.time(),
                str(kind),
                _js(observation),
                _js(interpretation),
                _js(evidence),
                str(decision or ""),
                str(reason or ""),
                str(model) if model is not None else None,
                _int_or_none(tokens),
                _int_or_none(latency_ms),
                _float_or_none(confidence),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def set_outcome(
    record_id: int,
    verdict: str,
    source: str = "",
    ts: Optional[float] = None,
    db_path: Optional[str] = None,
) -> bool:
    """The ONLY permitted mutation: attach an outcome to a record, exactly once.

    Returns True if the outcome was set, False if the record was missing or already
    had an outcome (the record stays immutable once judged).
    """
    if record_id is None:
        return False
    db = _resolve(db_path)
    try:
        conn = _connect(db)
    except Exception:
        return False
    try:
        cur = conn.execute(
            "UPDATE decision_records SET outcome = ?, outcome_ts = ?, outcome_source = ? "
            "WHERE id = ? AND outcome IS NULL",
            (
                str(verdict),
                float(ts) if ts is not None else time.time(),
                str(source or ""),
                int(record_id),
            ),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get(record_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    db = _resolve(db_path)
    try:
        conn = _connect(db)
    except Exception:
        return None
    try:
        row = conn.execute(
            "SELECT * FROM decision_records WHERE id = ?", (int(record_id),)
        ).fetchone()
        return _row_to_dict(row) if row else None
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def recent(
    limit: int = 50,
    kind: Optional[str] = None,
    only_unjudged: bool = False,
    db_path: Optional[str] = None,
) -> list:
    """Most-recent records first. Optionally filter by kind or to still-unjudged rows."""
    db = _resolve(db_path)
    try:
        conn = _connect(db)
    except Exception:
        return []
    try:
        clauses, params = [], []
        if kind:
            clauses.append("kind = ?")
            params.append(str(kind))
        if only_unjudged:
            clauses.append("outcome IS NULL")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        rows = conn.execute(
            "SELECT * FROM decision_records" + where + " ORDER BY ts DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def stats(db_path: Optional[str] = None) -> dict:
    """Counts by outcome — the seed of a later Cognition Score."""
    db = _resolve(db_path)
    out = {"total": 0, "judged": 0, "good": 0, "unnecessary": 0, "wrong": 0}
    try:
        conn = _connect(db)
    except Exception:
        return out
    try:
        out["total"] = conn.execute("SELECT COUNT(*) FROM decision_records").fetchone()[0]
        for verdict in ("good", "unnecessary", "wrong"):
            out[verdict] = conn.execute(
                "SELECT COUNT(*) FROM decision_records WHERE outcome = ?", (verdict,)
            ).fetchone()[0]
        out["judged"] = out["good"] + out["unnecessary"] + out["wrong"]
        return out
    except Exception:
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass
