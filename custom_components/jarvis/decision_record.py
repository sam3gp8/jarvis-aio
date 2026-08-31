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
    outcome_source TEXT,
    ref            TEXT
);
CREATE INDEX IF NOT EXISTS idx_dr_ts      ON decision_records (ts);
CREATE INDEX IF NOT EXISTS idx_dr_kind    ON decision_records (kind);
CREATE INDEX IF NOT EXISTS idx_dr_outcome ON decision_records (outcome);
CREATE INDEX IF NOT EXISTS idx_dr_ref     ON decision_records (ref);
"""


def _resolve(db_path: Optional[str]) -> str:
    return db_path or _DEFAULT_DB


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)   # idempotent; keeps every entry point self-contained
    try:  # migrate: add columns introduced after the initial schema
        cols = {r[1] for r in conn.execute("PRAGMA table_info(decision_records)").fetchall()}
        if "ref" not in cols:
            conn.execute("ALTER TABLE decision_records ADD COLUMN ref TEXT")
    except Exception:
        pass
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
    ref: Optional[str] = None,
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
            " model, tokens, latency_ms, confidence, ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                str(ref) if ref is not None else None,
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


def _apply_outcome(conn, rid, verdict, source, ts) -> bool:
    cur = conn.execute(
        "UPDATE decision_records SET outcome = ?, outcome_ts = ?, outcome_source = ? "
        "WHERE id = ? AND outcome IS NULL",
        (str(verdict), float(ts) if ts is not None else time.time(), str(source or ""), int(rid)),
    )
    conn.commit()
    return cur.rowcount > 0


def set_outcome_by_ref(ref, verdict, source: str = "", ts: Optional[float] = None,
                       db_path: Optional[str] = None) -> bool:
    """Set the outcome on the most recent still-unjudged record carrying `ref`
    (e.g. 'suggestion:11'). Used to link a dismissal/approval back to its decision."""
    if not ref:
        return False
    db = _resolve(db_path)
    try:
        conn = _connect(db)
    except Exception:
        return False
    try:
        row = conn.execute(
            "SELECT id FROM decision_records WHERE ref = ? AND outcome IS NULL "
            "ORDER BY ts DESC LIMIT 1", (str(ref),),
        ).fetchone()
        return _apply_outcome(conn, row[0], verdict, source, ts) if row else False
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def set_outcome_recent(kind: str, verdict, source: str = "", max_age: float = 3600.0,
                       ts: Optional[float] = None, db_path: Optional[str] = None) -> bool:
    """Set the outcome on the most recent still-unjudged record of `kind` within
    `max_age` seconds. Used for decisions with no stable id (e.g. an intrusion the
    user just called off)."""
    db = _resolve(db_path)
    try:
        conn = _connect(db)
    except Exception:
        return False
    try:
        cutoff = time.time() - float(max_age)
        row = conn.execute(
            "SELECT id FROM decision_records WHERE kind = ? AND outcome IS NULL AND ts >= ? "
            "ORDER BY ts DESC LIMIT 1", (str(kind), cutoff),
        ).fetchone()
        return _apply_outcome(conn, row[0], verdict, source, ts) if row else False
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
    if not Path(db).exists():
        return out          # not-yet-created store reads as empty — never created here
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


def calibration(db_path: Optional[str] = None, kind: Optional[str] = None,
                bins: int = 5, since: Optional[float] = None) -> dict:
    """Compare stated confidence against actual correctness over judged records.

    A judged record scores 1.0 when its outcome is OUTCOME_GOOD (JARVIS was right
    to act) and 0.0 otherwise (``wrong`` = false alarm, ``unnecessary`` = not
    needed). Records are grouped into equal-width confidence bins over [0, 1] and,
    per bin, we report how often they were actually good — so you can see whether
    e.g. 80%-confident decisions really come true ~80% of the time. Also reports a
    Brier score (mean squared error of confidence vs correctness; lower is better,
    0 is perfect) and the expected calibration error (ECE, the sample-weighted gap
    between confidence and reality across bins). Pure DB read; never raises.
    """
    db = _resolve(db_path)
    n = max(1, int(bins))
    out = {"n": 0, "good_rate": None, "brier": None, "ece": None,
           "bins": [], "kind": kind}
    if not Path(db).exists():
        return out
    try:
        conn = _connect(db)
    except Exception:
        return out
    try:
        sql = ("SELECT confidence, outcome FROM decision_records "
               "WHERE outcome IS NOT NULL AND confidence IS NOT NULL")
        params: list = []
        if kind:
            sql += " AND kind = ?"
            params.append(str(kind))
        if since is not None:
            sql += " AND ts >= ?"
            params.append(float(since))
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass

    pairs = []
    for r in rows:
        try:
            c = min(1.0, max(0.0, float(r[0])))
        except (TypeError, ValueError):
            continue
        pairs.append((c, 1.0 if r[1] == OUTCOME_GOOD else 0.0))

    total = len(pairs)
    out["n"] = total
    if total == 0:
        return out

    out["good_rate"] = round(sum(g for _, g in pairs) / total, 4)
    out["brier"] = round(sum((c - g) ** 2 for c, g in pairs) / total, 4)

    buckets: list = [[] for _ in range(n)]
    for c, g in pairs:
        buckets[min(n - 1, int(c * n))].append((c, g))
    ece = 0.0
    bins_out = []
    for i, b in enumerate(buckets):
        if b:
            mean_c = sum(c for c, _ in b) / len(b)
            rate = sum(g for _, g in b) / len(b)
            ece += (len(b) / total) * abs(mean_c - rate)
        else:
            mean_c = rate = None
        bins_out.append({
            "lo": round(i / n, 3), "hi": round((i + 1) / n, 3),
            "count": len(b),
            "mean_confidence": round(mean_c, 3) if mean_c is not None else None,
            "good_rate": round(rate, 3) if rate is not None else None,
        })
    out["ece"] = round(ece, 4)
    out["bins"] = bins_out
    return out


def interruption_budget(db_path: Optional[str] = None,
                        window_s: float = 86400.0, floor: float = 0.25) -> dict:
    """Judge whether JARVIS is interrupting without payoff, from recent outcomes.

    Over judged records in the last ``window_s`` seconds, the more that were judged
    ``unnecessary`` or ``wrong`` (noise / false alarm) rather than ``good``, the
    more JARVIS is intruding without value. Returns a ``multiplier`` between
    ``floor`` and 1.0 that the output gate can apply to its hourly announcement cap
    to interrupt less when recent interruptions have been unwelcome. Pure DB read;
    never raises. 1.0 (no change) when there's no data.
    """
    db = _resolve(db_path)
    out = {"window_s": window_s, "judged": 0, "good": 0, "unnecessary": 0,
           "wrong": 0, "unwelcome_rate": None, "multiplier": 1.0,
           "assessment": "no data"}
    if not Path(db).exists():
        return out
    since = time.time() - float(window_s)
    try:
        conn = _connect(db)
    except Exception:
        return out
    try:
        rows = conn.execute(
            "SELECT outcome, COUNT(*) FROM decision_records "
            "WHERE outcome IS NOT NULL AND ts >= ? GROUP BY outcome",
            (since,)).fetchall()
    except Exception:
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass

    counts = {"good": 0, "unnecessary": 0, "wrong": 0}
    for r in rows:
        if r[0] in counts:
            counts[r[0]] = int(r[1])
    judged = sum(counts.values())
    out.update(judged=judged, **counts)
    if judged == 0:
        return out

    rate = (counts["unnecessary"] + counts["wrong"]) / judged
    floor = min(1.0, max(0.0, float(floor)))
    out["unwelcome_rate"] = round(rate, 4)
    out["multiplier"] = round(max(floor, 1.0 - rate * (1.0 - floor)), 3)
    out["assessment"] = ("over-interrupting" if rate >= 0.5
                         else "borderline" if rate >= 0.25 else "healthy")
    return out


def outcome_rate(kind: str, window_s: Optional[float] = None,
                 db_path: Optional[str] = None) -> dict:
    """Outcome breakdown for judged records of one ``kind`` (optionally within
    the last ``window_s`` seconds). Pure DB read; never raises.

    Returns judged/good/unnecessary/wrong counts plus good_rate and
    unwelcome_rate (= (unnecessary + wrong) / judged), so a proactive surface can
    steer how selective it is from how its own past output was received.
    """
    db = _resolve(db_path)
    out = {"kind": kind, "judged": 0, "good": 0, "unnecessary": 0, "wrong": 0,
           "good_rate": None, "unwelcome_rate": None}
    if not Path(db).exists():
        return out
    try:
        conn = _connect(db)
    except Exception:
        return out
    try:
        sql = ("SELECT outcome, COUNT(*) FROM decision_records "
               "WHERE outcome IS NOT NULL AND kind = ?")
        params: list = [str(kind)]
        if window_s is not None:
            sql += " AND ts >= ?"
            params.append(time.time() - float(window_s))
        sql += " GROUP BY outcome"
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass
    counts = {"good": 0, "unnecessary": 0, "wrong": 0}
    for r in rows:
        if r[0] in counts:
            counts[r[0]] = int(r[1])
    judged = sum(counts.values())
    out.update(judged=judged, **counts)
    if judged:
        out["good_rate"] = round(counts["good"] / judged, 4)
        out["unwelcome_rate"] = round(
            (counts["unnecessary"] + counts["wrong"]) / judged, 4)
    return out
