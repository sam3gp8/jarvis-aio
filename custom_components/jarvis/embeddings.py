"""
JARVIS local semantic search via Ollama embeddings (v6.57.0).

The "all-in-one" answer to vector search. ChromaDB's embedded mode can't
install on Home Assistant's Python 3.14 (its onnxruntime dependency has no
3.14 wheel), so instead of bolting on a heavy native dependency that doesn't
build, JARVIS reuses the Ollama server it already talks to:

  - Embeddings come from Ollama's /api/embed (nomic-embed-text by default) —
    no API key, no Python package, no wheel to compile, works on any Python.
  - Vectors are stored in the same jarvis.db SQLite file, next to the FTS
    index — no new service, no ChromaDB.
  - Similarity is plain cosine over stored vectors, computed in stdlib Python.
    A few hundred chunks dotted against one query vector is trivial; no numpy.

This is genuinely more in the spirit of JARVIS-AIO than ChromaDB was: it
leans on infrastructure the user already runs and adds zero install weight.

Enable it from Settings; it degrades to keyword (FTS5) search whenever Ollama
or the embed model isn't reachable. Nothing here raises to the caller.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import struct
from typing import Optional

_LOGGER = logging.getLogger(__name__)

_DB_PATH = "/config/jarvis.db"
_DEFAULT_MODEL = "nomic-embed-text"
_TIMEOUT = 60            # embedding a batch on a cold model can be slow
_EMBED_BATCH = 32        # chunks per /api/embed call


def _cfg(key: str, default):
    try:
        from . import jarvis_config
        val = jarvis_config.get(key, default)
        return val if val is not None else default
    except Exception:
        return default


def is_enabled() -> bool:
    """Semantic search is on when the user opted in AND an Ollama base URL is
    configured (embeddings need somewhere to call)."""
    return bool(_cfg("semantic_search", False)) and bool(_ollama_base())


def _model() -> str:
    return str(_cfg("embed_model", _DEFAULT_MODEL)) or _DEFAULT_MODEL


def _ollama_base() -> Optional[str]:
    """Resolve the Ollama host from the same config the LLM layer uses. Accepts
    a bare host:port and returns a clean base (no trailing slash, no /v1)."""
    base = _cfg("embed_base_url", "") or _cfg("llm_base_url", "")
    base = str(base or "").strip().rstrip("/")
    if not base:
        return None
    if base.endswith("/v1"):        # strip the OpenAI-compat suffix if present
        base = base[:-3]
    return base


# ── SQLite vector store ──────────────────────────────────────────────────────

def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def init_store() -> bool:
    """Create the vector table if absent. Returns True on success."""
    try:
        conn = sqlite3.connect(_DB_PATH)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS doc_vectors ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  source TEXT NOT NULL,"
            "  chunk INTEGER NOT NULL,"
            "  content TEXT NOT NULL,"
            "  dim INTEGER NOT NULL,"
            "  vec BLOB NOT NULL,"
            "  model TEXT NOT NULL,"
            "  ingested TEXT NOT NULL"
            ")"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_vectors_source "
                     "ON doc_vectors(source)")
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        _LOGGER.warning("doc_vectors init failed: %s", exc)
        return False


def forget_source(source: str) -> None:
    try:
        conn = sqlite3.connect(_DB_PATH)
        conn.execute("DELETE FROM doc_vectors WHERE source = ?", (source,))
        conn.commit()
        conn.close()
    except Exception as exc:
        _LOGGER.debug("doc_vectors forget failed: %s", exc)


def store_vectors(source: str, chunks: list[str], vectors: list[list[float]],
                  ingested: str) -> int:
    """Persist chunk vectors for a source. Returns count stored."""
    if not chunks or not vectors or len(chunks) != len(vectors):
        return 0
    model = _model()
    try:
        conn = sqlite3.connect(_DB_PATH)
        conn.executemany(
            "INSERT INTO doc_vectors (source, chunk, content, dim, vec, model, "
            "ingested) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(source, i, chunks[i], len(vectors[i]), _pack(vectors[i]), model,
              ingested) for i in range(len(chunks))],
        )
        conn.commit()
        n = conn.total_changes
        conn.close()
        return len(chunks)
    except Exception as exc:
        _LOGGER.debug("doc_vectors store failed: %s", exc)
        return 0


def vector_count() -> int:
    try:
        conn = sqlite3.connect(_DB_PATH)
        n = conn.execute("SELECT COUNT(*) FROM doc_vectors").fetchone()[0]
        conn.close()
        return int(n)
    except Exception:
        return 0


# ── cosine similarity (pure Python) ──────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return -1.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def search_vectors(query_vec: list[float], k: int = 4) -> list[dict]:
    """Top-k stored chunks by cosine similarity to query_vec."""
    if not query_vec:
        return []
    try:
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT source, chunk, content, vec FROM doc_vectors").fetchall()
        conn.close()
    except Exception as exc:
        _LOGGER.debug("doc_vectors read failed: %s", exc)
        return []

    scored = []
    for r in rows:
        score = _cosine(query_vec, _unpack(r["vec"]))
        scored.append((score, r))
    scored.sort(key=lambda t: t[0], reverse=True)
    out = []
    for score, r in scored[:k]:
        if score <= 0:
            continue
        out.append({"text": r["content"], "source": r["source"],
                    "chunk": r["chunk"], "score": round(score, 3)})
    return out


# ── Ollama embedding calls (async, aiohttp — no new deps) ────────────────────

async def embed_texts(hass, texts: list[str]) -> Optional[list[list[float]]]:
    """Embed a list of strings via Ollama. Returns list of vectors, or None on
    any failure (caller falls back to keyword). Uses /api/embed (batch), with a
    fallback to the legacy /api/embeddings per-item for older Ollama."""
    base = _ollama_base()
    if not base or not texts:
        return None
    import aiohttp
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    session = async_get_clientsession(hass)
    model = _model()

    out: list[list[float]] = []
    for i in range(0, len(texts), _EMBED_BATCH):
        batch = texts[i:i + _EMBED_BATCH]
        vecs = await _embed_batch(session, base, model, batch)
        if vecs is None:
            return None
        out.extend(vecs)
    return out


async def _embed_batch(session, base, model, batch) -> Optional[list[list[float]]]:
    import aiohttp
    # Preferred: /api/embed (batch), Ollama >= 0.2.0
    try:
        async with session.post(
            f"{base}/api/embed",
            json={"model": model, "input": batch},
            timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
        ) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                embs = data.get("embeddings")
                if embs and len(embs) == len(batch):
                    return embs
            elif resp.status != 404:
                _LOGGER.debug("ollama /api/embed HTTP %s", resp.status)
    except Exception as exc:
        _LOGGER.debug("ollama /api/embed failed: %s", exc)

    # Fallback: legacy /api/embeddings (single prompt per call)
    out = []
    for text in batch:
        try:
            async with session.post(
                f"{base}/api/embeddings",
                json={"model": model, "prompt": text},
                timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                emb = data.get("embedding")
                if not emb:
                    return None
                out.append(emb)
        except Exception as exc:
            _LOGGER.debug("ollama /api/embeddings failed: %s", exc)
            return None
    return out


async def embed_one(hass, text: str) -> Optional[list[float]]:
    """Embed a single query string. None on failure."""
    res = await embed_texts(hass, [text])
    return res[0] if res else None


async def probe(hass) -> dict:
    """Check whether embeddings are reachable — for the Settings status/test.
    Returns {"ok", "model", "base", "dim"?, "error"?}. Never raises."""
    base = _ollama_base()
    if not base:
        return {"ok": False, "error": "no Ollama base URL configured "
                                      "(set llm_base_url to your Ollama host)"}
    vec = await embed_one(hass, "jarvis embedding health check")
    if not vec:
        return {"ok": False, "model": _model(), "base": base,
                "error": "Ollama did not return an embedding — is the embed "
                         "model pulled? Try: ollama pull " + _model()}
    return {"ok": True, "model": _model(), "base": base, "dim": len(vec)}
