"""Tests for the Document RAG agent (v6.55.0): the pure chunker, PDF-graceful
extraction routing, ingest/search through a simulated Chroma collection, and
the honest fallbacks. ChromaDB isn't installed in the test env, so the vector
path is exercised via a stub collection and the real FTS/none paths run as-is."""
import json

import pytest


@pytest.fixture
def docs(load):
    return load("documents")


# ── chunk_text (pure, the retrieval-quality core) ────────────────────────────

def test_short_text_is_one_chunk(docs):
    assert docs.chunk_text("furnace filter is 16x25x1") == ["furnace filter is 16x25x1"]


def test_empty_text_no_chunks(docs):
    assert docs.chunk_text("") == []
    assert docs.chunk_text("   \n\t ") == []


def test_long_text_splits_with_overlap(docs):
    text = " ".join(f"sentence{i}." for i in range(400))   # well over one chunk
    chunks = docs.chunk_text(text, size=300, overlap=60)
    assert len(chunks) > 1
    assert all(len(c) <= 320 for c in chunks)              # roughly bounded
    # overlap: end of chunk N shares text with start of chunk N+1
    joined = " ".join(chunks)
    for i in range(400):
        assert f"sentence{i}." in joined                   # nothing dropped


def test_chunk_prefers_paragraph_boundary(docs):
    a = "A" * 200
    b = "B" * 200
    text = a + "\n\n" + b
    chunks = docs.chunk_text(text, size=250, overlap=20)
    # first chunk should end at the paragraph break, not mid-A
    assert chunks[0].strip() == a


def test_chunk_collapses_whitespace(docs):
    out = docs.chunk_text("word   \t  word")
    assert out == ["word word"]


# ── extract_text routing ─────────────────────────────────────────────────────

def test_extract_txt(docs, tmp_path):
    f = tmp_path / "manual.txt"
    f.write_text("filter size 16x25x1")
    text, err = docs.extract_text(str(f))
    assert err is None and "16x25x1" in text


def test_extract_md(docs, tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("# Furnace\nModel ABC")
    text, err = docs.extract_text(str(f))
    assert err is None and "Model ABC" in text


def test_extract_unsupported_type(docs, tmp_path):
    f = tmp_path / "thing.xyz"
    f.write_text("data")
    _text, err = docs.extract_text(str(f))
    assert err and "unsupported" in err


def test_extract_pdf_without_lib_is_honest(docs, tmp_path, monkeypatch):
    # Simulate no PDF library installed: _extract_pdf should return a clear
    # message, not raise. (pypdf/pdfplumber/PyPDF2 absent in this env.)
    f = tmp_path / "manual.pdf"
    f.write_bytes(b"%PDF-1.4 not-really")
    text, err = docs.extract_text(str(f))
    assert text == ""
    assert err and ("PDF library" in err or "failed" in err)


# ── ingest + search through a stub Chroma collection ─────────────────────────

class _StubCollection:
    """Minimal in-memory stand-in for a Chroma collection."""
    def __init__(self):
        self.docs, self.metas, self.ids = [], [], []
    def add(self, documents, metadatas, ids):
        self.docs += documents; self.metas += metadatas; self.ids += ids
    def delete(self, where=None):
        if where and "source" in where:
            src = where["source"]
            keep = [(d, m, i) for d, m, i in zip(self.docs, self.metas, self.ids)
                    if m.get("source") != src]
            self.docs = [x[0] for x in keep]
            self.metas = [x[1] for x in keep]
            self.ids = [x[2] for x in keep]
    def query(self, query_texts, n_results, where=None):
        # naive keyword rank so search is deterministic in tests
        q = query_texts[0].lower()
        scored = []
        for d, m in zip(self.docs, self.metas):
            overlap = sum(1 for w in q.split() if w in d.lower())
            scored.append((overlap, d, m))
        scored.sort(key=lambda t: -t[0])
        top = [s for s in scored if s[0] > 0][:n_results]
        return {
            "documents": [[d for _, d, _ in top]],
            "metadatas": [[m for _, _, m in top]],
            "distances": [[0.1 for _ in top]],
        }
    def count(self):
        return len(self.docs)
    def get(self, include=None):
        return {"metadatas": self.metas}


@pytest.fixture
def stub_docs(docs, monkeypatch):
    col = _StubCollection()
    monkeypatch.setattr(docs, "_chroma_ok", True)
    monkeypatch.setattr(docs, "_collection", col)
    monkeypatch.setattr(docs, "_fts_ok", False)
    monkeypatch.setattr(docs, "_initialized", True)
    return docs, col


def test_ingest_file_chunks_and_stores(stub_docs, tmp_path):
    d, col = stub_docs
    f = tmp_path / "furnace.txt"
    f.write_text("The furnace filter size is 16x25x1. " * 40)   # forces chunks
    res = d.ingest_file(str(f))
    assert res["ok"] is True and res["chunks"] >= 1
    assert col.count() == res["chunks"]
    assert all(m["source"] == "furnace.txt" for m in col.metas)


def test_reingest_replaces_not_duplicates(stub_docs, tmp_path):
    d, col = stub_docs
    f = tmp_path / "receipt.txt"
    f.write_text("dishwasher purchased 2024-03-15 warranty 5 years")
    d.ingest_file(str(f))
    first = col.count()
    d.ingest_file(str(f))                # ingest again
    assert col.count() == first          # replaced, not doubled


def test_search_returns_relevant_chunk(stub_docs, tmp_path):
    d, col = stub_docs
    f = tmp_path / "furnace.txt"
    f.write_text("The furnace filter size is 16x25x1.\n\n"
                 "The water heater is set to 120 degrees.")
    d.ingest_file(str(f))
    hits = d.search_documents("furnace filter size", k=3)
    assert hits
    assert any("16x25x1" in h["text"] for h in hits)
    assert hits[0]["source"] == "furnace.txt"
    assert hits[0]["score"] is not None


def test_search_empty_query(stub_docs):
    d, _ = stub_docs
    assert d.search_documents("") == []
    assert d.search_documents("   ") == []


def test_library_status_reports_sources(stub_docs, tmp_path):
    d, _ = stub_docs
    (tmp_path / "a.txt").write_text("alpha content one two three")
    (tmp_path / "b.txt").write_text("bravo content four five six")
    d.ingest_file(str(tmp_path / "a.txt"))
    d.ingest_file(str(tmp_path / "b.txt"))
    st = d.library_status()
    assert st["chroma"] is True
    assert st["chunk_count"] >= 2
    names = {s["source"] for s in st["sources"]}
    assert {"a.txt", "b.txt"} <= names


def test_ingest_directory_summary(stub_docs, tmp_path):
    d, _ = stub_docs
    (tmp_path / "m1.txt").write_text("manual one filter details")
    (tmp_path / "m2.md").write_text("# manual two\nspecs here")
    (tmp_path / "ignore.log").write_text("not a supported type")
    res = d.ingest_directory(str(tmp_path))
    assert res["ok"] is True
    assert res["files_ingested"] == 2          # .log ignored
    assert res["files_seen"] == 2


def test_no_extractable_text_is_flagged(stub_docs, tmp_path):
    d, _ = stub_docs
    f = tmp_path / "blank.txt"
    f.write_text("   ")
    res = d.ingest_file(str(f))
    assert res["ok"] is False and "no extractable text" in res["error"]


# ── agent tool registration ──────────────────────────────────────────────────

def test_document_tools_registered(load):
    agent = load("agent")
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert {"search_documents", "ingest_documents"} <= names
    assert "search_documents" in agent._TOOL_MAP
    assert "ingest_documents" in agent._TOOL_MAP
