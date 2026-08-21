"""Tests for camera coverage inference (v7.19.0, Phase 2b). Covers JSON parsing
(valid, code-fenced, invented-room rejection, garbage) and the geometry-only
fallback thresholds. The LLM call itself is not exercised (needs a provider)."""
import pytest


@pytest.fixture
def cc(load):
    return load("camera_coverage")


def test_geometry_fallback_thresholds(cc):
    r = cc._geometry_result({"Dining": 1.0, "Living": 0.65, "Kitchen": 0.4})
    assert r["covered"] == ["Dining"]           # only >= 0.7 is "covered"
    assert r["source"] == "geometry"
    assert "Dining" in r["reason"]


def test_parse_valid_json(cc):
    r = cc._parse('{"covered":["Dining","Living"],"reason":"the whole room"}',
                  {"Dining": 1.0, "Living": 0.65})
    assert r["covered"] == ["Dining", "Living"]
    assert r["source"] == "llm"


def test_parse_strips_code_fences(cc):
    r = cc._parse('```json\n{"covered":["Dining"],"reason":"x"}\n```', {"Dining": 1.0})
    assert r and r["covered"] == ["Dining"]


def test_parse_rejects_invented_rooms(cc):
    r = cc._parse('{"covered":["Dining","Garage"],"reason":"x"}', {"Dining": 1.0})
    assert r["covered"] == ["Dining"]           # Garage is not a candidate — dropped


def test_parse_garbage_returns_none(cc):
    assert cc._parse("the camera sees stuff", {"Dining": 1.0}) is None
    assert cc._parse("", {"Dining": 1.0}) is None
