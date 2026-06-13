"""
JARVIS Long-Term Memory (v5.7.00).

Provides semantic search across past conversations so JARVIS can recall
context from previous interactions. Uses ChromaDB when available, falls
back to SQLite FTS5 keyword search.

Usage:
  - store_memory(text, metadata) — save a conversation turn
  - search_memory(query, k) — retrieve k most relevant past memories
  - get_conversation_context(query) — formatted context string for prompt injection

Storage: /config/jarvis_memory/ (ChromaDB) or jarvis.db FTS table (fallback)
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_LOGGER = logging.getLogger(__name__)

MEMORY_DIR = "/config/jarvis_memory"
_chromadb_available = False
_collection = None
_fts_available = False


# ── ChromaDB initialization ─────────────────────────────────────────────────

def _init_chromadb():
    """Try to initialize ChromaDB. Returns True on success."""
    global _chromadb_available, _collection
    try:
        import chromadb
        os.makedirs(MEMORY_DIR, exist_ok=True)
        client = chromadb.PersistentClient(path=MEMORY_DIR)
        _collection = client.get_or_create_collection(
            name="jarvis_memory",
            metadata={"hnsw:space": "cosine"},
        )
        _chromadb_available = True
        _LOGGER.info("JARVIS memory: ChromaDB initialized at %s", MEMORY_DIR)
        return True
    except ImportError:
        _LOGGER.debug("JARVIS memory: ChromaDB not available, using FTS5 fallback")
        return False
    except Exception as exc:
        _LOGGER.warning("JARVIS memory: ChromaDB init failed: %s", exc)
        return False


# ── SQLite FTS5 fallback ─────────────────────────────────────────────────────

def _init_fts():
    """Initialize FTS5 virtual table in the existing JARVIS database."""
    global _fts_available
    try:
        import sqlite3
        db_path = "/config/jarvis.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
            USING fts5(content, metadata, timestamp)
        """)
        conn.commit()
        conn.close()
        _fts_available = True
        _LOGGER.info("JARVIS memory: FTS5 fallback initialized")
        return True
    except Exception as exc:
        _LOGGER.warning("JARVIS memory: FTS5 init failed: %s", exc)
        return False


def _ensure_initialized():
    """Lazy init — try ChromaDB first, then FTS5."""
    global _chromadb_available, _fts_available
    if _chromadb_available or _fts_available:
        return
    if not _init_chromadb():
        _init_fts()


# ── Public API ───────────────────────────────────────────────────────────────

def store_memory(
    text: str,
    *,
    role: str = "user",
    device_id: str = "",
    conversation_id: str = "",
) -> bool:
    """
    Store a conversation turn in long-term memory.
    Called from conversation.py after each user/assistant message.
    """
    if not text or len(text.strip()) < 5:
        return False

    _ensure_initialized()
    ts = datetime.utcnow().isoformat()
    doc_id = f"{ts}_{role}_{hash(text) % 100000}"

    metadata = {
        "role": role,
        "device_id": device_id or "",
        "conversation_id": conversation_id or "",
        "timestamp": ts,
    }

    if _chromadb_available and _collection is not None:
        try:
            _collection.add(
                documents=[text],
                metadatas=[metadata],
                ids=[doc_id],
            )
            return True
        except Exception as exc:
            _LOGGER.debug("ChromaDB store failed: %s", exc)

    if _fts_available:
        try:
            import sqlite3, json
            conn = sqlite3.connect("/config/jarvis.db")
            conn.execute(
                "INSERT INTO memory_fts (content, metadata, timestamp) VALUES (?, ?, ?)",
                (text, json.dumps(metadata), ts),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as exc:
            _LOGGER.debug("FTS5 store failed: %s", exc)

    return False


def search_memory(
    query: str,
    k: int = 5,
    hours: Optional[int] = None,
) -> list[dict]:
    """
    Search long-term memory for relevant past conversations.

    Returns list of {"text": ..., "role": ..., "timestamp": ..., "score": ...}
    """
    if not query:
        return []

    _ensure_initialized()

    if _chromadb_available and _collection is not None:
        try:
            where = None
            if hours:
                cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
                where = {"timestamp": {"$gte": cutoff}}

            results = _collection.query(
                query_texts=[query],
                n_results=min(k, 20),
                where=where,
            )

            memories = []
            if results and results.get("documents"):
                docs = results["documents"][0]
                metas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(docs)
                dists = results["distances"][0] if results.get("distances") else [0] * len(docs)
                for doc, meta, dist in zip(docs, metas, dists):
                    memories.append({
                        "text": doc,
                        "role": meta.get("role", ""),
                        "timestamp": meta.get("timestamp", ""),
                        "score": round(1.0 - dist, 3),  # cosine distance → similarity
                    })
            return memories
        except Exception as exc:
            _LOGGER.debug("ChromaDB search failed: %s", exc)

    if _fts_available:
        try:
            import sqlite3, json
            conn = sqlite3.connect("/config/jarvis.db")
            conn.row_factory = sqlite3.Row
            # FTS5 MATCH query
            query_clean = " OR ".join(query.split()[:8])  # limit query terms
            sql = "SELECT content, metadata, rank FROM memory_fts WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?"
            rows = conn.execute(sql, (query_clean, k)).fetchall()
            conn.close()

            memories = []
            for row in rows:
                meta = {}
                try:
                    meta = json.loads(row["metadata"])
                except Exception:
                    pass
                memories.append({
                    "text": row["content"],
                    "role": meta.get("role", ""),
                    "timestamp": meta.get("timestamp", ""),
                    "score": round(abs(row["rank"]) * 0.1, 3) if row["rank"] else 0,
                })
            return memories
        except Exception as exc:
            _LOGGER.debug("FTS5 search failed: %s", exc)

    return []


def get_conversation_context(query: str, k: int = 3) -> str:
    """
    Format retrieved memories as a context string for the system prompt.
    Called from conversation.py before each LLM call.
    """
    memories = search_memory(query, k=k)
    if not memories:
        return ""

    parts = ["## Relevant past conversations"]
    for m in memories:
        ts = m.get("timestamp", "")[:16]  # trim seconds
        role = m.get("role", "")
        text = m.get("text", "")[:300]  # cap length
        parts.append(f"[{ts}] {role}: {text}")

    return "\n".join(parts)


def get_memory_stats() -> dict:
    """Return memory system stats for the panel."""
    _ensure_initialized()
    stats = {
        "backend": "none",
        "total_memories": 0,
    }

    if _chromadb_available and _collection is not None:
        try:
            stats["backend"] = "chromadb"
            stats["total_memories"] = _collection.count()
        except Exception:
            pass
    elif _fts_available:
        try:
            import sqlite3
            conn = sqlite3.connect("/config/jarvis.db")
            row = conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()
            conn.close()
            stats["backend"] = "fts5"
            stats["total_memories"] = row[0] if row else 0
        except Exception:
            pass

    return stats
