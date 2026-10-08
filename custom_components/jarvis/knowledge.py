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

from .paths import config_path_str

_LOGGER = logging.getLogger(__name__)

# SHADOW (#237): all_facts() also packages each curated fact as a
# kernel.provenance.Provenance (value / source / confidence / model) and logs a
# one-line summary — observe-only, nothing consumes it yet (conflict resolution
# and authoritative reads attach once it earns parity). Flip PROVENANCE_SHADOW
# to False to silence it; all_facts() returns exactly the same rows either way.
PROVENANCE_SHADOW = True

# SHADOW (Phase T, Deep World Model): all_facts() also folds the curated facts +
# their relation edges into a typed kernel.graph.KnowledgeGraph and logs a
# one-line summary — observe-only, nothing reads the graph yet. Flip
# GRAPH_SHADOW to False to silence it; all_facts() returns the same rows either
# way. (world_model.knowledge_graph() exposes the same view as a facade.)
GRAPH_SHADOW = True


def _emit_provenance_shadow(facts: list) -> None:
    """Log the provenance view of a fact set (shadow). Best-effort, never raises —
    the knowledge store itself is unchanged whether this runs or not."""
    try:
        from .kernel import provenance as P
        recs = []
        for f in (facts or []):
            if not isinstance(f, dict):
                continue
            recs.append(P.record(
                f.get("value"),
                source=str(f.get("source") or "knowledge"),
                confidence=float(f.get("confidence", 1.0) or 1.0),
                model=str(f.get("model") or ""),
            ))
        if recs:
            _LOGGER.debug("provenance(shadow): %d fact record(s); e.g. %s",
                          len(recs), P.summary(recs[0]))
    except Exception:   # pragma: no cover - defensive
        pass


def _emit_graph_shadow(facts: list) -> None:
    """Build the typed knowledge-graph view of the current facts + relations and
    log a one-line summary (Phase T — shadow). Best-effort, never raises — the
    knowledge store is unchanged whether this runs or not, and nothing reads the
    graph yet."""
    try:
        from .kernel import graph as G
        try:
            relations = related() or []
        except Exception:
            relations = []
        g = G.KnowledgeGraph.from_rows(facts or [], relations)
        if not g.is_empty():
            _LOGGER.debug("graph(shadow): %d entit(ies), %d relation(s)",
                          len(g.entities), len(g.relations))
    except Exception:   # pragma: no cover - defensive
        pass

DB_PATH = config_path_str("jarvis", "knowledge.db")

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
        # Shared connection factory — guarantees the handle closes even on an
        # error path, consistent with database.py / local_mind / scene_memory.
        from .sqlite_utils import ClosingConnection
        conn = sqlite3.connect(DB_PATH, timeout=10, factory=ClosingConnection)
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
    # migrate: add columns introduced after the initial schema
    cols = {r[1] for r in conn.execute("PRAGMA table_info(facts)").fetchall()}
    for col, decl in (("deleted_at", "REAL"), ("embedding", "BLOB")):
        if col not in cols:
            try:
                conn.execute(f"ALTER TABLE facts ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise  # only a concurrent-migration race is expected here
    # Relations graph (v8.3.0): typed edges between subjects/entities so JARVIS
    # can traverse how things relate ("Sam -> owns -> car.jeep", "kitchen ->
    # adjacent_to -> garage") rather than only storing flat facts. Soft-deleted
    # like facts, so a re-observed edge can't resurrect one the user removed.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS relations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            subject     TEXT NOT NULL,
            predicate   TEXT NOT NULL,
            object      TEXT NOT NULL,
            source      TEXT NOT NULL DEFAULT 'stated',
            confidence  REAL NOT NULL DEFAULT 1.0,
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL,
            deleted_at  REAL,
            UNIQUE(subject, predicate, object)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_subject ON relations(subject)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_object ON relations(object)")
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
        # BEGIN IMMEDIATE grabs the write lock before the read below, so a
        # concurrent forget() can't slip a tombstone in between our SELECT and
        # UPDATE/INSERT and get silently resurrected.
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id, source, deleted_at FROM facts WHERE subject = ? AND key = ?",
            (subject, key),
        ).fetchone()
        if existing:
            if existing["deleted_at"] is not None and source != "stated":
                # user deleted this fact; don't let re-observation resurrect it
                conn.commit()
                return None
            if respect_stated and existing["source"] == "stated" and source != "stated":
                row = conn.execute(
                    "SELECT * FROM facts WHERE id = ?", (existing["id"],)).fetchone()
                conn.commit()
                return _row_to_fact(row)  # don't clobber a user-stated fact
            conn.execute(
                """
                UPDATE facts SET value = ?, kind = ?, source = ?, confidence = ?,
                    salience = ?, updated_at = ?, expires_at = ?, deleted_at = NULL
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
                     created_at, updated_at, last_referenced, expires_at, deleted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (kind, subject, key, value, source, confidence, salience,
                 now, now, None, expires_at),
            )
            fid = cur.lastrowid
        row = conn.execute("SELECT * FROM facts WHERE id = ?", (fid,)).fetchone()
        conn.commit()
        _LOGGER.info("knowledge: remembered [%s] %s/%s = %r", kind, subject, key, value)
        return _row_to_fact(row)
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        _LOGGER.warning("knowledge: remember failed: %s", exc)
        return None
    finally:
        conn.close()


def forget(
    *,
    fact_id: Optional[int] = None,
    subject: Optional[str] = None,
    key: Optional[str] = None,
    now: Optional[float] = None,
) -> int:
    """
    Soft-delete by id, or by (subject, key): rows are tombstoned (deleted_at set),
    not removed, so a re-observed fact can't resurrect what the user deleted —
    only an explicit stated re-teach (see remember()) revives it. Returns rows
    affected. SYNC.
    """
    now = now if now is not None else time.time()
    conn = _connect()
    if conn is None:
        return 0
    try:
        with conn:
            if fact_id is not None:
                cur = conn.execute(
                    "UPDATE facts SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                    (now, fact_id))
            elif key is not None:
                if subject is not None:
                    cur = conn.execute(
                        "UPDATE facts SET deleted_at = ? "
                        "WHERE subject = ? AND key = ? AND deleted_at IS NULL",
                        (now, subject, key))
                else:
                    cur = conn.execute(
                        "UPDATE facts SET deleted_at = ? WHERE key = ? AND deleted_at IS NULL",
                        (now, key))
            else:
                return 0
            return cur.rowcount
    except Exception as exc:
        _LOGGER.warning("knowledge: forget failed: %s", exc)
        return 0
    finally:
        conn.close()


_TOMBSTONE_TTL = 365 * 86400.0  # keep deletion tombstones a year before hard-purging


def purge_expired(now: Optional[float] = None) -> int:
    """
    Remove facts past their expiry, and hard-delete tombstones (soft-deleted
    facts) older than _TOMBSTONE_TTL. Returns rows removed. SYNC.
    """
    now = now if now is not None else time.time()
    conn = _connect()
    if conn is None:
        return 0
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM facts WHERE expires_at IS NOT NULL AND expires_at < ? "
                "AND deleted_at IS NULL", (now,))
            removed = cur.rowcount
            cur = conn.execute(
                "DELETE FROM facts WHERE deleted_at IS NOT NULL AND deleted_at < ?",
                (now - _TOMBSTONE_TTL,))
            removed += cur.rowcount
            return removed
    except Exception as exc:
        _LOGGER.warning("knowledge: purge failed: %s", exc)
        return 0
    finally:
        conn.close()


# ── read ─────────────────────────────────────────────────────────────────────

def _live_rows(conn: sqlite3.Connection, subject: Optional[str], now: float,
               subjects: Optional[list] = None) -> list:
    sql = ("SELECT * FROM facts WHERE deleted_at IS NULL "
           "AND (expires_at IS NULL OR expires_at >= ?)")
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
        if PROVENANCE_SHADOW:
            _emit_provenance_shadow(facts)   # #237: observe-only
        if GRAPH_SHADOW:
            _emit_graph_shadow(facts)        # Phase T: observe-only
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
    return _format_block(facts)


def _format_block(facts: list[dict]) -> str:
    """Render a facts list as the "What you know" prompt block. Shared by the
    sync (keyword) and async (semantic) prompt builders."""
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


async def prompt_block_async(hass, query: str = "", *, subject: Optional[str] = None,
                             limit: int = 12, now: Optional[float] = None,
                             subjects: Optional[list] = None) -> str:
    """Semantic-aware "what you know" block: when embeddings are on and a query is
    given, facts are ranked by meaning (recall_semantic, which falls back to
    keyword recall on its own); otherwise this matches the sync prompt_block.
    Returns "" when empty so callers can concatenate unconditionally. ASYNC."""
    if query:
        facts = await recall_semantic(hass, query, subject=subject, k=limit,
                                      now=now, subjects=subjects)
    else:
        facts = await hass.async_add_executor_job(
            lambda: all_facts(subject=subject, now=now, subjects=subjects)[:limit])
    return _format_block(facts)


# ── relations graph (v8.3.0) ─────────────────────────────────────────────────

def _row_to_relation(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "subject": row["subject"],
        "predicate": row["predicate"],
        "object": row["object"],
        "source": row["source"],
        "confidence": round(row["confidence"], 3),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def relate(
    subject: str,
    predicate: str,
    obj: str,
    *,
    source: str = "stated",
    confidence: float = 1.0,
    now: Optional[float] = None,
) -> Optional[dict]:
    """Upsert a typed edge subject —predicate→ object (e.g. "sam", "owns",
    "car.jeep"). One row per (subject, predicate, object). A non-"stated" write
    will not resurrect an edge the user explicitly removed. SYNC — via executor."""
    subject = (subject or "").strip()
    predicate = (predicate or "").strip()
    obj = (obj or "").strip()
    if not subject or not predicate or not obj:
        return None
    if source not in SOURCES:
        source = "stated"
    now = now if now is not None else time.time()
    conn = _connect()
    if conn is None:
        return None
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id, deleted_at FROM relations "
            "WHERE subject = ? AND predicate = ? AND object = ?",
            (subject, predicate, obj),
        ).fetchone()
        if existing:
            if existing["deleted_at"] is not None and source != "stated":
                conn.commit()
                return None  # don't let re-observation resurrect a removed edge
            conn.execute(
                "UPDATE relations SET source = ?, confidence = ?, updated_at = ?, "
                "deleted_at = NULL WHERE id = ?",
                (source, confidence, now, existing["id"]),
            )
            rid = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO relations "
                "(subject, predicate, object, source, confidence, created_at, "
                " updated_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (subject, predicate, obj, source, confidence, now, now),
            )
            rid = cur.lastrowid
        row = conn.execute("SELECT * FROM relations WHERE id = ?", (rid,)).fetchone()
        conn.commit()
        return _row_to_relation(row)
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        _LOGGER.warning("knowledge: relate failed: %s", exc)
        return None
    finally:
        conn.close()


def unrelate(subject: str, predicate: Optional[str] = None,
             obj: Optional[str] = None, *, now: Optional[float] = None) -> int:
    """Soft-delete edges from ``subject`` (optionally narrowed by predicate and/or
    object). Returns rows affected. SYNC."""
    subject = (subject or "").strip()
    if not subject:
        return 0
    now = now if now is not None else time.time()
    conn = _connect()
    if conn is None:
        return 0
    try:
        sql = "UPDATE relations SET deleted_at = ? WHERE subject = ? AND deleted_at IS NULL"
        params: list = [now, subject]
        if predicate:
            sql += " AND predicate = ?"
            params.append(predicate)
        if obj:
            sql += " AND object = ?"
            params.append(obj)
        with conn:
            return conn.execute(sql, params).rowcount
    except Exception as exc:
        _LOGGER.warning("knowledge: unrelate failed: %s", exc)
        return 0
    finally:
        conn.close()


def related(subject: Optional[str] = None, *, obj: Optional[str] = None,
            predicate: Optional[str] = None) -> list[dict]:
    """Live edges matching any given end. With ``subject`` → its outgoing edges;
    with ``obj`` → its incoming edges; ``predicate`` narrows either. Newest
    first. SYNC — call via executor."""
    conn = _connect()
    if conn is None:
        return []
    try:
        sql = "SELECT * FROM relations WHERE deleted_at IS NULL"
        params: list = []
        if subject:
            sql += " AND subject = ?"
            params.append(subject.strip())
        if obj:
            sql += " AND object = ?"
            params.append(obj.strip())
        if predicate:
            sql += " AND predicate = ?"
            params.append(predicate.strip())
        sql += " ORDER BY updated_at DESC"
        return [_row_to_relation(r) for r in conn.execute(sql, params).fetchall()]
    except Exception as exc:
        _LOGGER.warning("knowledge: related failed: %s", exc)
        return []
    finally:
        conn.close()


# ── semantic recall (v8.3.0) ──────────────────────────────────────────────────
# Keyword recall (above) misses paraphrase — "who runs cold at night" won't match
# a fact keyed "sleep temperature preference". These helpers add embedding-based
# recall on top, reusing the same nomic-embed store the document RAG uses. They
# degrade gracefully: with embeddings disabled/unavailable, callers fall back to
# keyword recall(), so semantic recall is always safe to prefer.

def set_embedding(fact_id: int, vector: list) -> bool:
    """Store a packed embedding for one fact. SYNC — call via executor."""
    conn = _connect()
    if conn is None:
        return False
    try:
        from . import embeddings
        blob = embeddings._pack(vector)
        with conn:
            conn.execute("UPDATE facts SET embedding = ? WHERE id = ?", (blob, fact_id))
        return True
    except Exception as exc:
        _LOGGER.debug("knowledge: set_embedding failed: %s", exc)
        return False
    finally:
        conn.close()


def _live_rows_needing_embedding(subject: Optional[str], now: float,
                                 subjects: Optional[list], limit: int) -> list:
    conn = _connect()
    if conn is None:
        return []
    try:
        rows = _live_rows(conn, subject, now, subjects)
        return [r for r in rows if r["embedding"] is None][:max(0, limit)]
    except Exception:
        return []
    finally:
        conn.close()


async def remember_and_embed(hass, key: str, value: str, **kwargs) -> Optional[dict]:
    """remember() a fact, then compute + store its embedding so it is reachable by
    semantic recall. The embedding is best-effort: the fact is saved regardless.
    ASYNC — the DB writes run off-loop, the embed call is already async."""
    fact = await hass.async_add_executor_job(
        lambda: remember(key, value, **kwargs))
    if not fact:
        return fact
    try:
        from . import embeddings
        if embeddings.is_enabled():
            vec = await embeddings.embed_one(hass, f"{key}: {value}")
            if vec:
                await hass.async_add_executor_job(set_embedding, fact["id"], vec)
    except Exception as exc:
        _LOGGER.debug("knowledge: embed-on-write failed: %s", exc)
    return fact


async def recall_semantic(
    hass,
    query: str,
    *,
    subject: Optional[str] = None,
    k: int = 5,
    now: Optional[float] = None,
    subjects: Optional[list] = None,
    lazy_embed_limit: int = 25,
) -> list[dict]:
    """Retrieve the k facts most semantically similar to ``query`` by embedding
    cosine. Facts without an embedding yet are embedded lazily (bounded per call)
    so the store fills in over use. Falls back to keyword recall() when
    embeddings are disabled, the query can't be embedded, or nothing has an
    embedding. ASYNC."""
    now = now if now is not None else time.time()
    try:
        from . import embeddings
        if not embeddings.is_enabled():
            return await hass.async_add_executor_job(
                lambda: recall(query, subject=subject, k=k, now=now, subjects=subjects))
        # Backfill a bounded batch of un-embedded live facts first.
        pending = await hass.async_add_executor_job(
            _live_rows_needing_embedding, subject, now, subjects, lazy_embed_limit)
        for r in pending:
            try:
                vec = await embeddings.embed_one(hass, f"{r['key']}: {r['value']}")
                if vec:
                    await hass.async_add_executor_job(set_embedding, r["id"], vec)
            except Exception:
                break  # embedding backend went away — stop, use what we have
        q_vec = await embeddings.embed_one(hass, query) if query else None
        if not q_vec:
            return await hass.async_add_executor_job(
                lambda: recall(query, subject=subject, k=k, now=now, subjects=subjects))

        def _rank():
            conn = _connect()
            if conn is None:
                return []
            try:
                rows = _live_rows(conn, subject, now, subjects)
                scored = []
                for r in rows:
                    if r["embedding"] is None:
                        continue
                    try:
                        vec = embeddings._unpack(r["embedding"])
                        sim = embeddings._cosine(q_vec, vec)
                    except Exception:
                        continue
                    f = _row_to_fact(r)
                    f["similarity"] = round(sim, 4)
                    scored.append((sim, f))
                scored.sort(key=lambda s: s[0], reverse=True)
                return [f for _, f in scored[:max(1, k)]]
            finally:
                conn.close()

        top = await hass.async_add_executor_job(_rank)
        if top:
            return top
        # Nothing embedded / matched — keyword fallback keeps recall useful.
        return await hass.async_add_executor_job(
            lambda: recall(query, subject=subject, k=k, now=now, subjects=subjects))
    except Exception as exc:
        _LOGGER.debug("knowledge: recall_semantic failed: %s", exc)
        try:
            return await hass.async_add_executor_job(
                lambda: recall(query, subject=subject, k=k, now=now, subjects=subjects))
        except Exception:
            return []


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
