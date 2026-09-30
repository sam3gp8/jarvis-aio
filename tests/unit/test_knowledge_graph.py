"""Tests for the v8.3.0 knowledge additions: the relations graph and
embedding-based semantic recall (with keyword fallback)."""
import pytest


@pytest.fixture
def knowledge(load):
    return load("knowledge")


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch, knowledge):
    monkeypatch.setattr(knowledge, "DB_PATH", str(tmp_path / "knowledge.db"))
    yield


class _RecHass:
    async def async_add_executor_job(self, func, *a):
        return func(*a)


# ── relations graph ──────────────────────────────────────────────────────────

def test_relate_and_traverse(knowledge):
    knowledge.relate("sam", "owns", "car.jeep")
    knowledge.relate("sam", "owns", "bike.trek")
    knowledge.relate("kitchen", "adjacent_to", "garage")
    outgoing = knowledge.related(subject="sam")
    objs = {r["object"] for r in outgoing}
    assert objs == {"car.jeep", "bike.trek"}
    assert all(r["predicate"] == "owns" for r in outgoing)
    incoming = knowledge.related(obj="garage")
    assert incoming and incoming[0]["subject"] == "kitchen"


def test_relate_upsert_and_unrelate(knowledge):
    knowledge.relate("sam", "owns", "car.jeep", confidence=0.5)
    knowledge.relate("sam", "owns", "car.jeep", confidence=0.9)   # upsert, not dup
    rels = knowledge.related(subject="sam")
    assert len(rels) == 1 and rels[0]["confidence"] == 0.9
    assert knowledge.unrelate("sam", "owns", "car.jeep") == 1
    assert knowledge.related(subject="sam") == []


def test_removed_edge_not_resurrected_by_observation(knowledge):
    knowledge.relate("sam", "owns", "car.jeep", source="stated")
    knowledge.unrelate("sam", predicate="owns", obj="car.jeep")
    # a machine-observed re-assert must not revive a user-removed edge
    assert knowledge.relate("sam", "owns", "car.jeep", source="observed") is None
    assert knowledge.related(subject="sam") == []
    # but an explicit stated re-teach revives it
    assert knowledge.relate("sam", "owns", "car.jeep", source="stated") is not None
    assert len(knowledge.related(subject="sam")) == 1


# ── semantic recall ──────────────────────────────────────────────────────────

def _patch_embeddings(load, monkeypatch, vectors: dict, enabled=True):
    """Patch the real embeddings module's is_enabled + embed_one (keeping the real
    _pack/_unpack/_cosine). Patching the module's attributes — rather than swapping
    sys.modules — survives `from . import embeddings` binding the package attribute,
    so it works whether or not the real module was already imported."""
    emb = load("embeddings")

    async def embed_one(hass, text, _is_probe=False):
        return vectors.get(text)

    monkeypatch.setattr(emb, "is_enabled", lambda: enabled)
    monkeypatch.setattr(emb, "embed_one", embed_one)
    return emb


async def test_semantic_recall_ranks_by_meaning(knowledge, load, monkeypatch):
    knowledge.remember("sleep temperature", "Sam runs cold at night")
    knowledge.remember("trash day", "trash goes out Tuesday")
    vecs = {
        "sleep temperature: Sam runs cold at night": [1.0, 0.0, 0.0],
        "trash day: trash goes out Tuesday":         [0.0, 1.0, 0.0],
        "who feels chilly when sleeping":            [0.9, 0.1, 0.0],  # near the sleep fact
    }
    _patch_embeddings(load, monkeypatch, vecs)
    hits = await knowledge.recall_semantic(_RecHass(), "who feels chilly when sleeping", k=1)
    assert hits and hits[0]["key"] == "sleep temperature"
    assert hits[0]["similarity"] > 0.9


async def test_semantic_recall_falls_back_to_keyword_when_disabled(knowledge, load, monkeypatch):
    knowledge.remember("trash day", "trash goes out Tuesday")
    _patch_embeddings(load, monkeypatch, {}, enabled=False)
    hits = await knowledge.recall_semantic(_RecHass(), "trash", k=3)
    assert any(h["key"] == "trash day" for h in hits)   # keyword recall still works


async def test_remember_and_embed_stores_vector(knowledge, load, monkeypatch):
    vecs = {"pet name: the dog is called Rex": [0.1, 0.2, 0.3]}
    _patch_embeddings(load, monkeypatch, vecs)
    fact = await knowledge.remember_and_embed(_RecHass(), "pet name", "the dog is called Rex")
    assert fact and fact["key"] == "pet name"
    # the stored embedding is now used for semantic recall
    vecs["is there a dog"] = [0.1, 0.2, 0.29]
    hits = await knowledge.recall_semantic(_RecHass(), "is there a dog", k=1)
    assert hits and hits[0]["key"] == "pet name"
