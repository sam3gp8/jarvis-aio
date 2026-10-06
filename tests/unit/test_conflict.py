"""Tests for conflict resolution (Epistemic Fabric — pure).

Candidates are kernel.provenance.Provenance records; a fixed clock keeps recency
deterministic.
"""
import pytest


@pytest.fixture
def cf(load):
    return load("kernel.conflict")


@pytest.fixture
def pv(load):
    return load("kernel.provenance")


def test_empty_is_unresolved_and_total(cf):
    r = cf.resolve([], now=100.0)
    assert r.resolved is False and r.contested is False and r.winner is None


def test_higher_confidence_reliable_source_wins(cf, pv):
    cam = pv.record("empty", source="camera", confidence=0.6, now=lambda: 100.0)
    phone = pv.record("occupied", source="phone", confidence=0.9, now=lambda: 100.0)
    rels = {"camera": 0.9, "phone": 0.9}
    r = cf.resolve([cam, phone], reliabilities=rels, now=100.0, half_life=0)
    assert r.value == "occupied" and r.resolved is True and r.contested is False
    assert r.winner.source == "phone"


def test_unreliable_source_is_discounted(cf, pv):
    cam = pv.record("empty", source="camera", confidence=0.9, now=lambda: 100.0)
    flaky = pv.record("occupied", source="flaky", confidence=0.95, now=lambda: 100.0)
    rels = {"camera": 0.95, "flaky": 0.1}
    r = cf.resolve([cam, flaky], reliabilities=rels, now=100.0, half_life=0)
    assert r.value == "empty" and r.resolved is True


def test_recency_decays_older_evidence(cf, pv):
    old = pv.record("empty", source="s1", confidence=0.9, now=lambda: 0.0)       # 600s old
    new = pv.record("occupied", source="s2", confidence=0.7, now=lambda: 540.0)  # 60s old
    rels = {"s1": 1.0, "s2": 1.0}
    # half_life 300s: old weight ~0.9*0.25=0.225; new ~0.7*0.87=0.61 → new wins
    r = cf.resolve([old, new], reliabilities=rels, now=600.0, half_life=300.0)
    assert r.value == "occupied" and r.resolved is True


def test_agreeing_sources_reinforce(cf, pv):
    a = pv.record("empty", source="cam1", confidence=0.5, now=lambda: 100.0)
    b = pv.record("empty", source="cam2", confidence=0.5, now=lambda: 100.0)
    c = pv.record("occupied", source="phone", confidence=0.8, now=lambda: 100.0)
    rels = {"cam1": 1.0, "cam2": 1.0, "phone": 1.0}
    # empty: 0.5+0.5 = 1.0 summed; occupied: 0.8 → empty wins by reinforcement
    r = cf.resolve([a, b, c], reliabilities=rels, now=100.0, half_life=0)
    assert r.value == "empty" and r.resolved is True


def test_near_tie_is_contested_and_not_resolved(cf, pv):
    a = pv.record("empty", source="s1", confidence=0.80, now=lambda: 100.0)
    b = pv.record("occupied", source="s2", confidence=0.78, now=lambda: 100.0)
    rels = {"s1": 1.0, "s2": 1.0}
    r = cf.resolve([a, b], reliabilities=rels, now=100.0, half_life=0)
    assert r.contested is True and r.resolved is False
    assert r.value == "empty" and r.runner_up_value == "occupied"


def test_stale_candidates_excluded(cf, pv):
    stale = pv.record("occupied", source="s", confidence=0.99, now=lambda: 0.0, ttl=10.0)
    fresh = pv.record("empty", source="t", confidence=0.4, now=lambda: 90.0, ttl=100.0)
    r = cf.resolve([stale, fresh], reliabilities={"s": 1.0, "t": 1.0}, now=100.0, half_life=0)
    assert r.value == "empty"  # stale "occupied" dropped despite higher confidence


def test_corroboration_bonus(cf, pv):
    p = pv.record("empty", source="cam", confidence=0.5,
                  corroboration=["motion", "door"], now=lambda: 100.0)
    base = cf.score_candidate(pv.record("empty", source="cam", confidence=0.5,
                                        now=lambda: 100.0),
                              reliabilities={"cam": 1.0}, now=100.0, half_life=0)
    boosted = cf.score_candidate(p, reliabilities={"cam": 1.0}, now=100.0, half_life=0)
    assert boosted > base  # corroboration raises the weight


def test_resolution_round_trips_to_dict(cf, pv):
    a = pv.record("empty", source="cam", confidence=0.9, now=lambda: 100.0)
    r = cf.resolve([a], reliabilities={"cam": 1.0}, now=100.0, half_life=0)
    d = r.to_dict()
    assert d["value"] == "empty" and d["resolved"] is True and d["winner"]["source"] == "cam"
