"""Tests for local semantic search via Ollama embeddings (v6.57.0): the pure
vector math (pack/unpack/cosine), the SQLite vector store, and the async embed
path with a mocked Ollama HTTP session. No network and no chromadb needed."""
import math

import pytest


@pytest.fixture
def emb(load, tmp_path, monkeypatch):
    m = load("embeddings")
    # point the store at a temp DB so tests don't touch /config
    monkeypatch.setattr(m, "_DB_PATH", str(tmp_path / "jarvis.db"))
    return m


# ── pack / unpack round-trip ─────────────────────────────────────────────────

def test_pack_unpack_roundtrip(emb):
    vec = [0.1, -0.5, 3.14159, 0.0, 100.0]
    out = emb._unpack(emb._pack(vec))
    assert len(out) == len(vec)
    for a, b in zip(vec, out):
        assert abs(a - b) < 1e-5


# ── cosine similarity ────────────────────────────────────────────────────────

def test_cosine_identical_is_one(emb):
    v = [1.0, 2.0, 3.0]
    assert abs(emb._cosine(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal_is_zero(emb):
    assert abs(emb._cosine([1.0, 0.0], [0.0, 1.0])) < 1e-6


def test_cosine_opposite_is_negative(emb):
    assert emb._cosine([1.0, 0.0], [-1.0, 0.0]) < 0


def test_cosine_bad_input(emb):
    assert emb._cosine([], [1.0]) == -1.0
    assert emb._cosine([0.0, 0.0], [1.0, 1.0]) == -1.0    # zero norm


# ── SQLite vector store + search ─────────────────────────────────────────────

def test_store_and_search_ranks_by_similarity(emb):
    assert emb.init_store()
    # three orthogonal-ish vectors
    emb.store_vectors("manual.txt",
                      ["furnace filter", "water heater", "garage door"],
                      [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                      "2026-01-01")
    assert emb.vector_count() == 3
    # query closest to the first vector
    hits = emb.search_vectors([0.9, 0.1, 0.0], k=2)
    assert hits
    assert hits[0]["text"] == "furnace filter"
    assert hits[0]["source"] == "manual.txt"
    assert hits[0]["score"] > hits[-1]["score"] if len(hits) > 1 else True


def test_forget_source_removes_vectors(emb):
    emb.init_store()
    emb.store_vectors("a.txt", ["x"], [[1.0, 0.0]], "t")
    emb.store_vectors("b.txt", ["y"], [[0.0, 1.0]], "t")
    assert emb.vector_count() == 2
    emb.forget_source("a.txt")
    assert emb.vector_count() == 1
    hits = emb.search_vectors([1.0, 0.0], k=5)
    assert all(h["source"] != "a.txt" for h in hits)


def test_search_empty_when_no_vectors(emb):
    emb.init_store()
    assert emb.search_vectors([1.0, 0.0], k=4) == []


def test_store_mismatched_lengths_is_noop(emb):
    emb.init_store()
    assert emb.store_vectors("x", ["a", "b"], [[1.0]], "t") == 0


# ── is_enabled gating ────────────────────────────────────────────────────────

def test_is_enabled_requires_flag_and_url(emb, monkeypatch):
    cfg = {}
    monkeypatch.setattr(emb, "_cfg", lambda k, d: cfg.get(k, d))
    assert emb.is_enabled() is False                       # nothing set
    cfg["semantic_search"] = True
    assert emb.is_enabled() is False                       # flag but no URL
    cfg["llm_base_url"] = "http://gpu.local:11434"
    assert emb.is_enabled() is True                        # both present


def test_ollama_base_strips_v1_suffix(emb, monkeypatch):
    monkeypatch.setattr(emb, "_cfg",
                        lambda k, d: "http://gpu.local:11434/v1" if k == "llm_base_url" else d)
    assert emb._ollama_base() == "http://gpu.local:11434"


# ── async embed with mocked Ollama session ───────────────────────────────────

class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self, content_type=None): return self._payload


class _FakeSession:
    def __init__(self, handler):
        self._handler = handler
    def post(self, url, json=None, timeout=None):
        return self._handler(url, json)


async def test_embed_texts_uses_batch_endpoint(emb, fake_hass, monkeypatch):
    monkeypatch.setattr(emb, "_ollama_base", lambda: "http://ollama.local:11434")

    def handler(url, payload):
        assert url.endswith("/api/embed")                  # prefers batch endpoint
        n = len(payload["input"])
        return _FakeResp(200, {"embeddings": [[0.1, 0.2, 0.3]] * n})

    import homeassistant.helpers.aiohttp_client as ac
    monkeypatch.setattr(ac, "async_get_clientsession",
                        lambda h: _FakeSession(handler), raising=False)

    vecs = await emb.embed_texts(fake_hass, ["one", "two", "three"])
    assert vecs is not None and len(vecs) == 3
    assert vecs[0] == [0.1, 0.2, 0.3]


async def test_embed_falls_back_to_legacy_on_404(emb, fake_hass, monkeypatch):
    monkeypatch.setattr(emb, "_ollama_base", lambda: "http://ollama.local:11434")

    def handler(url, payload):
        if url.endswith("/api/embed"):
            return _FakeResp(404, {})                      # batch not supported
        assert url.endswith("/api/embeddings")             # legacy per-item
        return _FakeResp(200, {"embedding": [0.5, 0.6]})

    import homeassistant.helpers.aiohttp_client as ac
    monkeypatch.setattr(ac, "async_get_clientsession",
                        lambda h: _FakeSession(handler), raising=False)

    vecs = await emb.embed_texts(fake_hass, ["a", "b"])
    assert vecs == [[0.5, 0.6], [0.5, 0.6]]


async def test_embed_returns_none_without_base(emb, fake_hass, monkeypatch):
    monkeypatch.setattr(emb, "_ollama_base", lambda: None)
    assert await emb.embed_texts(fake_hass, ["x"]) is None


async def test_probe_ok_reports_dim(emb, fake_hass, monkeypatch):
    monkeypatch.setattr(emb, "_ollama_base", lambda: "http://ollama.local:11434")
    async def _one(hass, text): return [0.0] * 768
    monkeypatch.setattr(emb, "embed_one", _one)
    res = await emb.probe(fake_hass)
    assert res["ok"] is True and res["dim"] == 768


async def test_probe_no_base_is_honest(emb, fake_hass, monkeypatch):
    monkeypatch.setattr(emb, "_ollama_base", lambda: None)
    res = await emb.probe(fake_hass)
    assert res["ok"] is False and "no Ollama base URL" in res["error"]


# ── documents async retrieval wiring ─────────────────────────────────────────

def test_document_tools_still_registered(load):
    agent = load("agent")
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert {"search_documents", "ingest_documents"} <= names
