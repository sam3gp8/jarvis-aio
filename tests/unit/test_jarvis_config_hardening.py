"""Regression tests for v6.48.0 config hardening: a hand-edited
/config/jarvis/config.json that is invalid JSON, or valid JSON whose top
level isn't an object, took down integration setup (get_all() raised before
the panel registered → 'Unable to load custom panel'). The file must be
sidelined, defaults must load, and every accessor must survive."""
import json
from pathlib import Path

import pytest


@pytest.fixture
def jcfg(load, tmp_path, monkeypatch):
    j = load("jarvis_config")
    monkeypatch.setattr(j, "CONFIG_PATH", Path(tmp_path / "config.json"))
    # reset module state between tests
    monkeypatch.setattr(j, "_cache", {})
    monkeypatch.setattr(j, "_loaded", False)
    monkeypatch.setattr(j, "last_load_error", None)
    return j


def _corrupt_files(j):
    return list(j.CONFIG_PATH.parent.glob("config.json.corrupt-*"))


def test_valid_dict_loads_normally(jcfg):
    jcfg.CONFIG_PATH.write_text('{"honorific": "sir", "camera_overrides": {"a": "b"}}')
    out = jcfg.load()
    assert out["honorific"] == "sir"
    assert jcfg.last_load_error is None
    assert _corrupt_files(jcfg) == []


def test_top_level_list_is_sidelined_not_fatal(jcfg):
    """The live failure shape: config.json parsed but wasn't an object —
    dict(_cache) / _cache.get then raised inside async_setup_entry."""
    jcfg.CONFIG_PATH.write_text('[{"camera_overrides": {"a": "b"}}]')
    out = jcfg.load()
    assert out == {}
    assert jcfg.last_load_error and "list" in jcfg.last_load_error
    assert not jcfg.CONFIG_PATH.exists()          # moved aside, not deleted
    kept = _corrupt_files(jcfg)
    assert len(kept) == 1
    assert "camera_overrides" in kept[0].read_text()   # edits preserved
    # every accessor keeps working on defaults
    assert jcfg.get("anything", "dflt") == "dflt"
    assert jcfg.get_all() == {}


def test_invalid_json_is_sidelined_not_fatal(jcfg):
    jcfg.CONFIG_PATH.write_text('{"camera_overrides": {"a": "b",}}')   # trailing comma
    out = jcfg.load()
    assert out == {}
    assert jcfg.last_load_error and "invalid JSON" in jcfg.last_load_error
    assert len(_corrupt_files(jcfg)) == 1


def test_top_level_string_is_sidelined(jcfg):
    jcfg.CONFIG_PATH.write_text('"just a string"')
    assert jcfg.load() == {}
    assert jcfg.last_load_error and "str" in jcfg.last_load_error


def test_set_after_corruption_recovers_and_persists(jcfg):
    jcfg.CONFIG_PATH.write_text('[1, 2, 3]')
    jcfg.load()
    jcfg.set("honorific", "sir")                  # must not raise
    assert jcfg.get("honorific") == "sir"
    assert json.loads(jcfg.CONFIG_PATH.read_text())["honorific"] == "sir"


def test_runtime_cache_poisoning_self_heals(jcfg, monkeypatch):
    # Even if something replaces the cache at runtime, accessors recover.
    jcfg.CONFIG_PATH.write_text('{"k": "v"}')
    jcfg.load()
    monkeypatch.setattr(jcfg, "_cache", ["poisoned"])
    assert jcfg.get("k", "dflt") == "dflt"        # reset to {}, no raise
    jcfg.set("k2", "v2")
    assert jcfg.get("k2") == "v2"


def test_missing_file_stays_clean(jcfg):
    assert jcfg.load() == {}
    assert jcfg.last_load_error is None
    assert jcfg.get("x", 1) == 1
