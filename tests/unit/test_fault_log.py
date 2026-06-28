"""Regression tests for FaultLog (diagnostics fault history).

Stdlib-only on-disk store; tested against tmp_path. Pins commit/query, tag
matching, the rolling 1000-cap, persistence across instances, and tolerance of
a missing or corrupt buffer file. (Relocated from the former memory/vector_store
module, which was removed because it shadowed the real top-level memory.py.)
"""
import importlib.util
import pathlib
import sys

import pytest

COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"


def _load_standalone(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, COMP / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fl = _load_standalone("jarvis_fault_log", "diagnostics/fault_log.py")


@pytest.fixture
def log(tmp_path):
    return fl.FaultLog(path=str(tmp_path / "faults.json"))


def test_commit_then_query_by_text(log):
    log.commit_event("Root storage critically high at 97 percent", tags=["root storage"])
    log.commit_event("Core network switch offline", tags=["the core network switch"])
    hits = log.query_related_faults(["storage"])
    assert len(hits) == 1
    assert "storage" in hits[0]["text"].lower()


def test_query_matches_on_tags(log):
    log.commit_event("a generic event", tags=["basement freeze sensor"])
    assert len(log.query_related_faults(["freeze"])) == 1


def test_query_empty_keywords_returns_nothing(log):
    log.commit_event("x", tags=["y"])
    assert log.query_related_faults([]) == []


def test_persistence_across_instances(tmp_path):
    path = str(tmp_path / "f.json")
    a = fl.FaultLog(path=path)
    a.commit_event("event one", ["tag"])
    b = fl.FaultLog(path=path)  # fresh instance, same file
    assert len(b.query_related_faults(["event"])) == 1


def test_rolling_cap_keeps_most_recent(tmp_path):
    log = fl.FaultLog(path=str(tmp_path / "f.json"), max_entries=5)
    for i in range(20):
        log.commit_event(f"event {i}", ["e"])
    kept = log.recent(100)
    assert len(kept) == 5
    assert [e["text"] for e in kept] == [f"event {i}" for i in range(15, 20)]


def test_missing_file_is_empty(tmp_path):
    log = fl.FaultLog(path=str(tmp_path / "nope.json"))
    assert log.query_related_faults(["x"]) == []
    assert log.recent() == []


def test_corrupt_file_is_tolerated(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not valid json")
    log = fl.FaultLog(path=str(bad))
    assert log.recent() == []          # doesn't raise
    log.commit_event("recovered", ["t"])  # can still write over it
    assert len(log.query_related_faults(["recovered"])) == 1


def test_commit_returns_stored_entry(log):
    entry = log.commit_event("Hello", tags=["Greeting", "MixedCase"])
    assert entry["text"] == "Hello"
    assert entry["tags"] == ["greeting", "mixedcase"]  # normalised lower-case
    assert isinstance(entry["ts"], float)
