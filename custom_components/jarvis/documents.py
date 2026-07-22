"""
JARVIS Document RAG agent (v6.55.0).

The blueprint's "Document Analytics Agent": semantic retrieval over the
household's manuals and receipts, so JARVIS can answer "what's the filter size
for the furnace?" or "when did we buy the dishwasher?" from your own paperwork
instead of guessing.

Design, mirroring memory.py's proven ChromaDB approach exactly:
  - A SEPARATE Chroma collection ('jarvis_documents') in the same persistent
    client. Documents are a different corpus from conversation turns — a manual
    is not a chat message, and a furnace-spec query should not surface old
    conversations, nor should "what did I say yesterday" surface the manual.
  - Same default embedding function Chroma uses for memory (documents are
    embedded internally on add/query — no extra model dependency), cosine space.
  - FTS5 fallback in the existing jarvis.db when ChromaDB isn't installed, so
    keyword retrieval still works on a minimal install.

Ingestion source: /config/jarvis/documents/ — drop PDFs or .txt files there
(via the HA File editor, Samba, etc.) and JARVIS ingests them. No upload UI
needed; it uses infrastructure the user already has, exactly like config.json.

PDF text extraction degrades honestly: it tries pypdf, then pdfplumber, then
PyPDF2; if none is installed it says so and skips that file rather than
crashing — plain-text files always work regardless. Nothing here ever raises
to the caller.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

_LOGGER = logging.getLogger(__name__)

DOCS_DIR = "/config/jarvis/documents"
MEMORY_DIR = "/config/jarvis_memory"          # same Chroma client as memory.py
_COLLECTION_NAME = "jarvis_documents"
_DB_PATH = "/config/jarvis.db"

_CHUNK_CHARS = 900            # ~1 chunk ≈ a paragraph or two — good recall granularity
_CHUNK_OVERLAP = 150         # carry context across chunk boundaries
_MAX_FILE_MB = 25            # skip absurdly large files
_SUPPORTED = (".pdf", ".txt", ".md")

_chroma_ok = False
_collection = None
_fts_ok = False
_initialized = False


# ── init (mirrors memory.py) ─────────────────────────────────────────────────

def _init_chroma() -> bool:
    global _chroma_ok, _collection
    try:
        import chromadb
        client = chromadb.PersistentClient(path=MEMORY_DIR)
        _collection = client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        _chroma_ok = True
        _LOGGER.info("JARVIS documents: ChromaDB collection ready at %s", MEMORY_DIR)
        return True
    except ImportError:
        _LOGGER.debug("JARVIS documents: ChromaDB not available, FTS5 fallback")
        return False
    except Exception as exc:
        _LOGGER.warning("JARVIS documents: ChromaDB init failed: %s", exc)
        return False


def _init_fts() -> bool:
    global _fts_ok
    try:
        import sqlite3
        conn = sqlite3.connect(_DB_PATH)
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS document_fts "
            "USING fts5(content, source, chunk_id, ingested)"
        )
        conn.commit()
        conn.close()
        _fts_ok = True
        _LOGGER.info("JARVIS documents: FTS5 fallback ready")
        return True
    except Exception as exc:
        _LOGGER.warning("JARVIS documents: FTS5 init failed: %s", exc)
        return False


def _ensure_init() -> None:
    global _initialized
    if _initialized:
        return
    if not _init_chroma():
        _init_fts()
    _initialized = True


# ── PDF text extraction (graceful across libs / none) ────────────────────────

def extract_text(path: str) -> tuple[str, Optional[str]]:
    """
    (text, error). Plain text/markdown read directly; PDFs via whichever of
    pypdf / pdfplumber / PyPDF2 is installed. error is a human string when we
    couldn't read it (missing lib, unreadable) — text is "" in that case.
    """
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext in (".txt", ".md"):
            return p.read_text(encoding="utf-8", errors="ignore"), None
        if ext == ".pdf":
            return _extract_pdf(path)
        return "", f"unsupported type {ext}"
    except Exception as exc:
        return "", f"read error: {exc}"


def _extract_pdf(path: str) -> tuple[str, Optional[str]]:
    # pypdf (current), then pdfplumber (better layout), then PyPDF2 (legacy).
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        text = "\n".join((pg.extract_text() or "") for pg in reader.pages)
        return text, None
    except ImportError:
        pass
    except Exception as exc:
        return "", f"pypdf failed: {exc}"
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            text = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
        return text, None
    except ImportError:
        pass
    except Exception as exc:
        return "", f"pdfplumber failed: {exc}"
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(path)
        text = "\n".join((pg.extract_text() or "") for pg in reader.pages)
        return text, None
    except ImportError:
        pass
    except Exception as exc:
        return "", f"PyPDF2 failed: {exc}"
    return "", ("no PDF library installed — add 'pypdf' to read PDFs "
                "(plain .txt/.md files work without it)")


# ── chunking (pure) ──────────────────────────────────────────────────────────

def chunk_text(text: str, size: int = _CHUNK_CHARS,
               overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split into overlapping chunks on paragraph/sentence-ish boundaries.
    Pure and deterministic — the retrieval-quality core, fully testable."""
    text = re.sub(r"[ \t]+", " ", (text or "")).strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            # prefer to break at a paragraph, then sentence, then space
            window = text[start:end]
            for sep in ("\n\n", "\n", ". ", " "):
                idx = window.rfind(sep)
                if idx > size * 0.5:          # don't make a tiny chunk
                    end = start + idx + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def _doc_id(source: str, idx: int, chunk: str) -> str:
    h = hashlib.sha1(f"{source}:{idx}:{chunk[:60]}".encode()).hexdigest()[:12]
    return f"{source}__{idx}__{h}"


# ── ingest ───────────────────────────────────────────────────────────────────

def _forget_source(source: str) -> None:
    """Remove all chunks for a source before re-ingesting (idempotent re-scan)."""
    if _chroma_ok and _collection is not None:
        try:
            _collection.delete(where={"source": source})
        except Exception as exc:
            _LOGGER.debug("doc forget (chroma) failed: %s", exc)
    if _fts_ok:
        try:
            import sqlite3
            conn = sqlite3.connect(_DB_PATH)
            conn.execute("DELETE FROM document_fts WHERE source = ?", (source,))
            conn.commit()
            conn.close()
        except Exception as exc:
            _LOGGER.debug("doc forget (fts) failed: %s", exc)


def ingest_file(path: str) -> dict:
    """Ingest one document file into keyword (FTS) search. Returns
    {"source", "chunks", "ok", "chunk_texts"?, "error"?}. Never raises.
    chunk_texts is included on success so an async caller can also embed them
    for semantic search (v6.57.0)."""
    _ensure_init()
    source = os.path.basename(path)
    text, err = extract_text(path)
    if err:
        return {"source": source, "chunks": 0, "ok": False, "error": err}
    chunks = chunk_text(text)
    if not chunks:
        return {"source": source, "chunks": 0, "ok": False,
                "error": "no extractable text (scanned image PDF?)"}

    _forget_source(source)
    ingested = datetime.utcnow().isoformat()

    if _chroma_ok and _collection is not None:
        try:
            _collection.add(
                documents=chunks,
                metadatas=[{"source": source, "chunk": i, "ingested": ingested}
                           for i in range(len(chunks))],
                ids=[_doc_id(source, i, c) for i, c in enumerate(chunks)],
            )
            return {"source": source, "chunks": len(chunks), "ok": True,
                    "chunk_texts": chunks, "ingested": ingested}
        except Exception as exc:
            _LOGGER.debug("doc chroma add failed: %s", exc)

    if _fts_ok:
        try:
            import sqlite3
            conn = sqlite3.connect(_DB_PATH)
            conn.executemany(
                "INSERT INTO document_fts (content, source, chunk_id, ingested) "
                "VALUES (?, ?, ?, ?)",
                [(c, source, str(i), ingested) for i, c in enumerate(chunks)],
            )
            conn.commit()
            conn.close()
            return {"source": source, "chunks": len(chunks), "ok": True,
                    "chunk_texts": chunks, "ingested": ingested}
        except Exception as exc:
            return {"source": source, "chunks": 0, "ok": False,
                    "error": f"fts store failed: {exc}"}

    return {"source": source, "chunks": 0, "ok": False,
            "error": "no vector or FTS store available"}


async def ingest_directory_async(hass, directory: str = DOCS_DIR) -> dict:
    """Ingest the documents folder, adding Ollama-embedded vectors when semantic
    search is enabled (v6.57.0). Always does keyword (FTS) ingest; layers vector
    embeddings on top when available. Falls back silently to keyword-only if
    Ollama is unreachable."""
    from . import embeddings
    base = ingest_directory(directory)          # FTS ingest (sync)
    if not base.get("ok") or not embeddings.is_enabled():
        base["semantic"] = False
        return base

    embeddings.init_store()
    embedded_files = 0
    embedded_chunks = 0
    semantic_error = None
    for res in base.get("files", []):
        if not res.get("ok") or not res.get("chunk_texts"):
            continue
        source = res["source"]
        chunks = res["chunk_texts"]
        vecs = await embeddings.embed_texts(hass, chunks)
        if vecs is None:
            semantic_error = ("Ollama embeddings unavailable — pull the embed "
                              "model and check the host; keyword search is active")
            break
        embeddings.forget_source(source)
        stored = await hass.async_add_executor_job(
            embeddings.store_vectors, source, chunks, vecs,
            res.get("ingested", ""))
        if stored:
            embedded_files += 1
            embedded_chunks += stored

    base["semantic"] = embedded_files > 0
    base["embedded_files"] = embedded_files
    base["embedded_chunks"] = embedded_chunks
    if semantic_error:
        base["semantic_error"] = semantic_error
    return base


async def search_documents_async(hass, query: str, k: int = 4) -> list[dict]:
    """Retrieve document chunks, preferring Ollama semantic search when enabled,
    falling back to keyword (FTS) otherwise (v6.57.0)."""
    from . import embeddings
    if embeddings.is_enabled():
        qvec = await embeddings.embed_one(hass, query)
        if qvec:
            hits = await hass.async_add_executor_job(
                embeddings.search_vectors, qvec, k)
            if hits:
                for h in hits:
                    h["engine"] = "semantic"
                return hits
    # fall back to keyword
    hits = search_documents(query, k)
    for h in hits:
        h["engine"] = "keyword"
    return hits


def ingest_directory(directory: str = DOCS_DIR) -> dict:
    """Ingest every supported file in the documents directory. Returns a
    summary with per-file results. Creates the directory if missing."""
    _ensure_init()
    d = Path(directory)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return {"ok": False, "error": f"cannot access {directory}: {exc}",
                "files": [], "total_chunks": 0}

    results = []
    total_chunks = 0
    for f in sorted(d.iterdir()):
        if not f.is_file() or f.suffix.lower() not in _SUPPORTED:
            continue
        try:
            if f.stat().st_size > _MAX_FILE_MB * 1_000_000:
                results.append({"source": f.name, "chunks": 0, "ok": False,
                                "error": f"larger than {_MAX_FILE_MB}MB — skipped"})
                continue
        except Exception:
            pass
        res = ingest_file(str(f))
        results.append(res)
        total_chunks += res.get("chunks", 0)

    ok_files = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "files": results, "files_ingested": ok_files,
            "files_seen": len(results), "total_chunks": total_chunks,
            "directory": directory}


# ── retrieval ────────────────────────────────────────────────────────────────

def search_documents(query: str, k: int = 4) -> list[dict]:
    """Top-k document chunks for a query.
    Returns [{"text", "source", "chunk", "score"}]. Never raises."""
    if not query or not query.strip():
        return []
    _ensure_init()

    if _chroma_ok and _collection is not None:
        try:
            res = _collection.query(query_texts=[query], n_results=min(k, 20))
            out = []
            if res and res.get("documents"):
                docs = res["documents"][0]
                metas = res["metadatas"][0] if res.get("metadatas") else [{}] * len(docs)
                dists = res["distances"][0] if res.get("distances") else [0] * len(docs)
                for doc, meta, dist in zip(docs, metas, dists):
                    out.append({
                        "text": doc,
                        "source": meta.get("source", ""),
                        "chunk": meta.get("chunk", 0),
                        "score": round(1.0 - dist, 3),
                    })
            return out
        except Exception as exc:
            _LOGGER.debug("doc chroma search failed: %s", exc)

    if _fts_ok:
        try:
            import sqlite3
            conn = sqlite3.connect(_DB_PATH)
            conn.row_factory = sqlite3.Row
            terms = " OR ".join(re.findall(r"\w+", query)[:8]) or query
            rows = conn.execute(
                "SELECT content, source, chunk_id, rank FROM document_fts "
                "WHERE document_fts MATCH ? ORDER BY rank LIMIT ?",
                (terms, k),
            ).fetchall()
            conn.close()
            return [{"text": r["content"], "source": r["source"],
                     "chunk": r["chunk_id"], "score": None} for r in rows]
        except Exception as exc:
            _LOGGER.debug("doc fts search failed: %s", exc)

    return []


def library_status() -> dict:
    """What's in the library — backends and counts, for status/UI. Never raises."""
    _ensure_init()
    info = {"chroma": _chroma_ok, "fts": _fts_ok, "directory": DOCS_DIR,
            "chunk_count": 0, "sources": []}
    if _chroma_ok and _collection is not None:
        try:
            info["chunk_count"] = _collection.count()
            got = _collection.get(include=["metadatas"])
            srcs = {}
            for m in (got.get("metadatas") or []):
                s = m.get("source", "?")
                srcs[s] = srcs.get(s, 0) + 1
            info["sources"] = [{"source": s, "chunks": n}
                               for s, n in sorted(srcs.items())]
        except Exception as exc:
            _LOGGER.debug("doc status (chroma) failed: %s", exc)
    elif _fts_ok:
        try:
            import sqlite3
            conn = sqlite3.connect(_DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT source, COUNT(*) n FROM document_fts GROUP BY source"
            ).fetchall()
            conn.close()
            info["chunk_count"] = sum(r["n"] for r in rows)
            info["sources"] = [{"source": r["source"], "chunks": r["n"]} for r in rows]
        except Exception as exc:
            _LOGGER.debug("doc status (fts) failed: %s", exc)
    return info
