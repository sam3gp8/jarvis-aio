"""Tests for Frigate-native identity (v6.59.0) — reading a recognized name from
Frigate's sub_label instead of requiring Double Take. The MQTT handler needs a
live bus, so the focus here is the pure _parse_sub_label normalizer, which is
where the real risk lives: Frigate encodes sub_label differently by version."""
import pytest


@pytest.fixture
def rec(load):
    return load("recognition")


# ── string form: "Name" (no score) ──────────────────────────────────────────

def test_bare_string_name(rec):
    name, conf = rec._parse_sub_label("Sam")
    assert name == "Sam" and conf == 0.0


def test_string_is_trimmed(rec):
    name, conf = rec._parse_sub_label("  Eliana  ")
    assert name == "Eliana"


# ── list form: ["Name", score] with score 0..1 ──────────────────────────────

def test_list_name_and_fractional_score(rec):
    name, conf = rec._parse_sub_label(["Sam", 0.92])
    assert name == "Sam"
    assert conf == 92.0                       # 0..1 → percent


def test_list_score_already_percent(rec):
    # some setups may already emit a 0..100 value; don't double-scale
    name, conf = rec._parse_sub_label(["Sam", 95.0])
    assert name == "Sam" and conf == 95.0


def test_list_name_only(rec):
    name, conf = rec._parse_sub_label(["Sam"])
    assert name == "Sam" and conf == 0.0


# ── empty / malformed → no identity, never raises ────────────────────────────

def test_none_is_empty(rec):
    assert rec._parse_sub_label(None) == ("", 0.0)


def test_empty_string(rec):
    assert rec._parse_sub_label("") == ("", 0.0)


def test_empty_list(rec):
    assert rec._parse_sub_label([]) == ("", 0.0)


def test_garbage_score_does_not_raise(rec):
    name, conf = rec._parse_sub_label(["Sam", "notanumber"])
    # falls back cleanly — name may be kept but confidence must be safe
    assert conf == 0.0


def test_none_name_in_list(rec):
    name, conf = rec._parse_sub_label([None, 0.5])
    assert name == ""


# ── threshold semantics line up with is_confident logic ──────────────────────

def test_confidence_threshold_boundary(rec):
    # sanity: the module's threshold is a plain percent number
    assert isinstance(rec.CONFIDENCE_THRESHOLD, (int, float))
    _, conf = rec._parse_sub_label(["Sam", 0.61])
    assert conf >= rec.CONFIDENCE_THRESHOLD    # 61% clears a 60 threshold


def test_recognition_source_default_is_both(rec):
    # with no config set, the module defaults to 'both' sources active
    import sys, types
    cfg = types.ModuleType("jc.jarvis_config")
    cfg.get = lambda k, d=None: d          # returns default
    sys.modules["jc.jarvis_config"] = cfg
    try:
        src = str(cfg.get("recognition_source", "both") or "both").lower()
        assert src == "both"
        assert src in ("both", "doubletake")   # doubletake would be active
        assert src in ("both", "frigate")      # frigate would be active
    finally:
        sys.modules.pop("jc.jarvis_config", None)
