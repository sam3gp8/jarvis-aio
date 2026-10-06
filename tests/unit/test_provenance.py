"""Tests for provenance (Epistemic Fabric — pure).

No Home Assistant, no DB: plain records and a fixed clock.
"""
import pytest


@pytest.fixture
def pv(load):
    return load("kernel.provenance")


def test_confidence_clamped(pv):
    assert pv.Provenance(value=1, confidence=2.0).confidence == 1.0
    assert pv.Provenance(value=1, confidence=-1).confidence == 0.0


def test_record_stamps_and_expires(pv):
    p = pv.record(False, source="camera_2", confidence=0.94, model="vision-v4",
                  now=lambda: 100.0, ttl=300.0)
    assert p.value is False and p.source == "camera_2" and p.model == "vision-v4"
    assert p.observed_ts == 100.0 and p.expires_ts == 400.0
    assert p.is_fresh(now=399.0) and not p.is_expired(now=399.0)
    assert p.is_expired(now=400.0) and not p.is_fresh(now=400.0)
    assert p.age(now=130.0) == pytest.approx(30.0)
    # No ttl → never expires.
    assert pv.record(1, now=lambda: 0.0).is_expired(now=1e12) is False


def test_select_authoritative_prefers_fresh_high_confidence(pv):
    a = pv.record("empty", source="cam", confidence=0.6, now=lambda: 10.0, ttl=100.0)
    b = pv.record("empty", source="motion", confidence=0.9, now=lambda: 20.0, ttl=100.0)
    stale = pv.record("occupied", source="old", confidence=0.99, now=lambda: 0.0, ttl=5.0)
    best = pv.select_authoritative([a, b, stale], now=50.0)
    assert best is b  # highest-confidence fresh record (stale one excluded)


def test_select_authoritative_none_when_all_stale(pv):
    stale = pv.record(1, confidence=0.9, now=lambda: 0.0, ttl=1.0)
    assert pv.select_authoritative([stale], now=100.0) is None
    assert pv.select_authoritative([], now=1.0) is None


def test_corroborate_same_value_reinforces(pv):
    a = pv.record("empty", source="cam", confidence=0.6, now=lambda: 10.0)
    b = pv.record("empty", source="motion", confidence=0.6, now=lambda: 20.0)
    merged = pv.corroborate(a, b)
    assert merged.value == "empty"
    assert merged.confidence == pytest.approx(0.84)  # 1-(1-.6)(1-.6)
    assert merged.source == "motion"              # newer leads
    assert "cam" in merged.corroboration          # older folded into corroboration


def test_corroborate_conflicting_values_returns_stronger(pv):
    a = pv.record("empty", source="cam", confidence=0.6)
    b = pv.record("occupied", source="phone", confidence=0.8)
    out = pv.corroborate(a, b)
    assert out is b  # different values: not corroboration, higher confidence wins


def test_summary_is_descriptive(pv):
    p = pv.record(False, source="camera_2", confidence=0.94, model="vision-v4",
                  corroboration=["motion_2"], now=lambda: 100.0, ttl=300.0)
    s = pv.summary(p, now=130.0)
    assert "camera_2" in s and "0.94" in s and "vision-v4" in s
    assert "motion_2" in s and "fresh" in s
    assert pv.summary(None) == "provenance: none"


def test_round_trips_through_dict(pv):
    p = pv.record({"k": 1}, source="s", confidence=0.5, model="m",
                  corroboration=["x", "y"], now=lambda: 7.0, ttl=10.0)
    back = pv.Provenance.from_dict(p.to_dict())
    assert back == p
