"""Regression tests for LocalSemanticMemory.

Stdlib-only on-disk store; tested against tmp_path. Pins commit/query, tag
matching, the rolling 1000-cap, persistence across instances, and tolerance of
a missing or corrupt buffer file.
"""
import importlib.util
import pathlib
import sys

import pytest

COMP = pathlib.Path(__file__).resolve().parents[2] / "jarvis_assistant" / "jarvis_component"


def _load_standalone(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, COMP / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


vs = _load_standalone("jarvis_vector_store", "memory/vector_store.py")


@pytest.fixture
def mem(tmp_path):
    return vs.LocalSemanticMemory(path=str(tmp_path / "mem.json"))


def test_commit_then_query_by_text(mem):
    mem.commit_event("Root storage critically high at 97 percent", tags=["root storage"])
    mem.commit_event("Core network switch offline", tags=["the core network switch"])
    hits = mem.query_related_faults(["storage"])
    assert len(hits) == 1
    assert "storage" in hits[0]["text"].lower()


def test_query_matches_on_tags(mem):
    mem.commit_event("a generic event", tags=["basement freeze sensor"])
    assert len(mem.query_related_faults(["freeze"])) == 1


def test_query_empty_keywords_returns_nothing(mem):
    mem.commit_event("x", tags=["y"])
    assert mem.query_related_faults([]) == []


def test_persistence_across_instances(tmp_path):
    path = str(tmp_path / "m.json")
    a = vs.LocalSemanticMemory(path=path)
    a.commit_event("event one", ["tag"])
    b = vs.LocalSemanticMemory(path=path)  # fresh instance, same file
    assert len(b.query_related_faults(["event"])) == 1


def test_rolling_cap_keeps_most_recent(tmp_path):
    mem = vs.LocalSemanticMemory(path=str(tmp_path / "m.json"), max_entries=5)
    for i in range(20):
        mem.commit_event(f"event {i}", ["e"])
    kept = mem.recent(100)
    assert len(kept) == 5
    assert [e["text"] for e in kept] == [f"event {i}" for i in range(15, 20)]


def test_missing_file_is_empty(tmp_path):
    mem = vs.LocalSemanticMemory(path=str(tmp_path / "nope.json"))
    assert mem.query_related_faults(["x"]) == []
    assert mem.recent() == []


def test_corrupt_file_is_tolerated(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not valid json")
    mem = vs.LocalSemanticMemory(path=str(bad))
    assert mem.recent() == []          # doesn't raise
    mem.commit_event("recovered", ["t"])  # can still write over it
    assert len(mem.query_related_faults(["recovered"])) == 1


def test_commit_returns_stored_entry(mem):
    entry = mem.commit_event("Hello", tags=["Greeting", "MixedCase"])
    assert entry["text"] == "Hello"
    assert entry["tags"] == ["greeting", "mixedcase"]  # normalised lower-case
    assert isinstance(entry["ts"], float)
