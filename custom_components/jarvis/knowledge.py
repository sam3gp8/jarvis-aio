"""
JARVIS Knowledge — curated semantic memory (v6.25.0).

This is the *semantic* memory layer: durable, curated facts and preferences that
JARVIS knows and can reason over — distinct from two stores that already exist:

  • memory.py        episodic transcript recall ("what we said before")
  • patterns.db      high-volume behavioural telemetry ("what tends to happen")

knowledge.py holds the low-volume, high-value middle: discrete facts a butler
would simply *know* — "trash is Tuesday", "Sam runs cold at night", "Eliana's
pickup is 3 PM today". Each is attributed to a subject so per-person identity can
slot in later untouched, carries a source + confidence so observed/inferred facts
rank below stated ones, and can expire so ephemeral facts clean themselves up.

Storage: /config/jarvis/knowledge.db (sibling of patterns.db). Pure SQLite —
keyword + recency + salience recall now; an embedding column can be added later
for semantic search without reshaping callers.

All DB functions are SYNC — call them via hass.async_add_executor_job(...).
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from typing import Optional

_LOGGER = logging.getLogger(__name__)

DB_PATH = "/config/jarvis/knowledge.db"

KINDS = ("fact", "preference", "event", "profile")
SOURCES = ("stated", "observed", "inferred")
DEFAULT_SUBJECT = "household"

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "to", "in", "on", "at",
    "for", "and", "or", "my", "your", "our", "i", "me", "do", "does", "what",
    "that", "this", "it", "with", "about", "they", "them", "their",
}


# ── connection / schema ──────────────────────────────────────────────────────

def _connect() -> Optional[sqlite3.Connection]:
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        return conn
    except Exception as exc:
        _LOGGER.warning("knowledge: connect failed: %s", exc)
        return None


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            kind            TEXT NOT NULL DEFAULT 'fact',
            subject         TEXT NOT NULL DEFAULT 'household',
            key             TEXT NOT NULL,
            value           TEXT NOT NULL,
            source          TEXT NOT NULL DEFAULT 'stated',
            confidence      REAL NOT NULL DEFAULT 1.0,
            salience        REAL NOT NULL DEFAULT 1.0,
            created_at      REAL NOT NULL,
            updated_at      REAL NOT NULL,
            last_referenced REAL,
            expires_at      REAL,
            UNIQUE(subject, key)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject)")
    conn.commit()


def _row_to_fact(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "subject": row["subject"],
        "key": row["key"],
        "value": row["value"],
        "source": row["source"],
        "confidence": round(row["confidence"], 3),
        "salience": round(row["salience"], 3),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_referenced": row["last_referenced"],
        "expires_at": row["expires_at"],
    }


def _tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if t not in _STOPWORDS and len(t) > 1}


# ── write ────────────────────────────────────────────────────────────────────

def remember(
    key: str,
    value: str,
    *,
    subject: str = DEFAULT_SUBJECT,
    kind: str = "fact",
    source: str = "stated",
    confidence: float = 1.0,
    salience: float = 1.0,
    ttl_seconds: Optional[float] = None,
    respect_stated: bool = False,
    now: Optional[float] = None,
) -> Optional[dict]:
    """
    Upsert a fact. One value per (subject, key) — re-teaching the same key updates
    it in place (and a stated fact overrides a previously observed/inferred one).

    respect_stated: when True, an incoming non-"stated" write (e.g. an observation
    from the pattern analyzer) will NOT overwrite an existing fact the user
    explicitly stated — the stated fact is returned unchanged. This keeps machine
    inference from clobbering things the user told us directly.

    Returns the stored fact, or None on failure. SYNC — call via executor.
    """
    key = (key or "").strip()
    value = (value or "").strip()
    if not key or not value:
        return None
    if kind not in KINDS:
        kind = "fact"
    if source not in SOURCES:
        source = "stated"
    now = now if now is not None else time.time()
    expires_at = (now + ttl_seconds) if ttl_seconds else None
    subject = (subject or DEFAULT_SUBJECT).strip() or DEFAULT_SUBJECT

    conn = _connect()
    if conn is None:
        return None
    try:
        with conn:
            existing = conn.execute(
                "SELECT id, source FROM facts WHERE subject = ? AND key = ?",
                (subject, key),
            ).fetchone()
            if existing:
                if respect_stated and existing["source"] == "stated" and source != "stated":
                    row = conn.execute(
                        "SELECT * FROM facts WHERE id = ?", (existing["id"],)).fetchone()
                    return _row_to_fact(row)  # don't clobber a user-stated fact
                conn.execute(
                    """
                    UPDATE facts SET value = ?, kind = ?, source = ?, confidence = ?,
                        salience = ?, updated_at = ?, expires_at = ?
                    WHERE id = ?
                    """,
                    (value, kind, source, confidence, salience, now, expires_at, existing["id"]),
                )
                fid = existing["id"]
            else:
                cur = conn.execute(
                    """
                    INSERT INTO facts
                        (kind, subject, key, value, source, confidence, salience,
                         created_at, updated_at, last_referenced, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (kind, subject, key, value, source, confidence, salience,
                     now, now, None, expires_at),
                )
                fid = cur.lastrowid
            row = conn.execute("SELECT * FROM facts WHERE id = ?", (fid,)).fetchone()
        _LOGGER.info("knowledge: remembered [%s] %s/%s = %r", kind, subject, key, value)
        return _row_to_fact(row)
    except Exception as exc:
        _LOGGER.warning("knowledge: remember failed: %s", exc)
        return None
    finally:
        conn.close()


def forget(
    *,
    fact_id: Optional[int] = None,
    subject: Optional[str] = None,
    key: Optional[str] = None,
) -> int:
    """Delete by id, or by (subject, key). Returns rows removed. SYNC."""
    conn = _connect()
    if conn is None:
        return 0
    try:
        with conn:
            if fact_id is not None:
                cur = conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            elif key is not None:
                if subject is not None:
                    cur = conn.execute(
                        "DELETE FROM facts WHERE subject = ? AND key = ?", (subject, key))
                else:
                    cur = conn.execute("DELETE FROM facts WHERE key = ?", (key,))
            else:
                return 0
            return cur.rowcount
    except Exception as exc:
        _LOGGER.warning("knowledge: forget failed: %s", exc)
        return 0
    finally:
        conn.close()


def purge_expired(now: Optional[float] = None) -> int:
    """Remove facts past their expiry. Returns rows removed. SYNC."""
    now = now if now is not None else time.time()
    conn = _connect()
    if conn is None:
        return 0
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM facts WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))
            return cur.rowcount
    except Exception as exc:
        _LOGGER.warning("knowledge: purge failed: %s", exc)
        return 0
    finally:
        conn.close()


# ── read ─────────────────────────────────────────────────────────────────────

def _live_rows(conn: sqlite3.Connection, subject: Optional[str], now: float,
               subjects: Optional[list] = None) -> list:
    sql = "SELECT * FROM facts WHERE (expires_at IS NULL OR expires_at >= ?)"
    params: list = [now]
    if subjects is not None:
        placeholders = ",".join("?" for _ in subjects) or "''"
        sql += f" AND subject IN ({placeholders})"
        params.extend(subjects)
    elif subject is not None:
        sql += " AND subject = ?"
        params.append(subject)
    return conn.execute(sql, params).fetchall()


def all_facts(subject: Optional[str] = None, now: Optional[float] = None,
               subjects: Optional[list] = None) -> list[dict]:
    """All non-expired facts (optionally for one subject), newest first. SYNC."""
    now = now if now is not None else time.time()
    conn = _connect()
    if conn is None:
        return []
    try:
        rows = _live_rows(conn, subject, now, subjects)
        facts = [_row_to_fact(r) for r in rows]
        facts.sort(key=lambda f: f["updated_at"], reverse=True)
        return facts
    except Exception as exc:
        _LOGGER.warning("knowledge: all_facts failed: %s", exc)
        return []
    finally:
        conn.close()


def recall(
    query: str = "",
    *,
    subject: Optional[str] = None,
    k: int = 5,
    now: Optional[float] = None,
    touch: bool = True,
    subjects: Optional[list] = None,
) -> list[dict]:
    """
    Retrieve the k most relevant facts. Scored by query-term overlap (key+value),
    then salience·confidence, then recency. Empty query → most salient/recent.
    Bumps last_referenced on returned facts so referenced knowledge stays warm.
    SYNC — call via executor.
    """
    now = now if now is not None else time.time()
    conn = _connect()
    if conn is None:
        return []
    try:
        rows = _live_rows(conn, subject, now, subjects)
        if not rows:
            return []
        q_tokens = _tokens(query)
        scored = []
        for r in rows:
            f = _row_to_fact(r)
            text_tokens = _tokens(f["key"] + " " + f["value"])
            overlap = len(q_tokens & text_tokens)
            match = (overlap / len(q_tokens)) if q_tokens else 0.0
            if q_tokens and overlap == 0:
                continue  # a real query that hits nothing is not relevant
            age_days = max(0.0, (now - f["updated_at"]) / 86400.0)
            recency = 1.0 / (1.0 + age_days)
            score = (3.0 * match) + (f["salience"] * f["confidence"]) + (0.5 * recency)
            scored.append((score, f))
        scored.sort(key=lambda s: s[0], reverse=True)
        top = [f for _, f in scored[:max(1, k)]]
        if touch and top:
            ids = [f["id"] for f in top]
            with conn:
                conn.executemany(
                    "UPDATE facts SET last_referenced = ? WHERE id = ?",
                    [(now, i) for i in ids],
                )
        return top
    except Exception as exc:
        _LOGGER.warning("knowledge: recall failed: %s", exc)
        return []
    finally:
        conn.close()


# ── prompt injection ─────────────────────────────────────────────────────────

_SUBJECT_LABEL = {"household": "Household", "primary": "About the primary resident"}


def prompt_block(query: str = "", *, subject: Optional[str] = None,
                 limit: int = 12, now: Optional[float] = None,
                 subjects: Optional[list] = None) -> str:
    """
    A compact "what you know" block for the system prompt. If a query is given,
    the most relevant facts; otherwise the most salient. Returns "" when empty so
    callers can concatenate unconditionally.
    """
    facts = (recall(query, subject=subject, k=limit, now=now, touch=False, subjects=subjects)
             if query else all_facts(subject=subject, now=now, subjects=subjects)[:limit])
    if not facts:
        return ""
    by_subject: dict = {}
    for f in facts:
        by_subject.setdefault(f["subject"], []).append(f)
    lines = ["## What you know"]
    for subj, items in by_subject.items():
        lines.append(_SUBJECT_LABEL.get(subj, subj))
        for f in items:
            hedge = "" if f["source"] == "stated" and f["confidence"] >= 0.9 else " (~)"
            lines.append(f"- {f['key']}: {f['value']}{hedge}")
    return "\n".join(lines)


# ── stats (panel) ────────────────────────────────────────────────────────────

def stats(now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    conn = _connect()
    if conn is None:
        return {"total": 0, "by_kind": {}, "by_subject": {}}
    try:
        rows = _live_rows(conn, None, now)
        by_kind: dict = {}
        by_subject: dict = {}
        for r in rows:
            by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
            by_subject[r["subject"]] = by_subject.get(r["subject"], 0) + 1
        return {"total": len(rows), "by_kind": by_kind, "by_subject": by_subject}
    except Exception as exc:
        _LOGGER.warning("knowledge: stats failed: %s", exc)
        return {"total": 0, "by_kind": {}, "by_subject": {}}
    finally:
        conn.close()
