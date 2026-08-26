"""Offline replay / policy evaluation over Decision Records — v7.48.0."""
from __future__ import annotations

import pytest


@pytest.fixture
def replay(load):
    return load("replay")


def _rec(conf, outcome):
    return {"confidence": conf, "outcome": outcome}


# ── bucketing ─────────────────────────────────────────────────────────────────
def test_threshold_buckets(replay):
    recs = [
        _rec(0.9, "right"),   # acted & right  -> kept_right
        _rec(0.8, "wrong"),   # acted & wrong  -> kept_wrong
        _rec(0.2, "right"),   # held  & right  -> suppressed_right (good call lost)
        _rec(0.1, "wrong"),   # held  & wrong  -> suppressed_wrong (mistake avoided)
    ]
    r = replay.evaluate_threshold(recs, 0.5)
    assert (r.kept_right, r.kept_wrong, r.suppressed_right, r.suppressed_wrong) == (1, 1, 1, 1)
    assert r.total == 4
    assert r.correct == 2                      # kept_right + suppressed_wrong
    assert r.accuracy == 0.5
    assert r.mistakes_avoided == 1
    assert r.good_calls_lost == 1


def test_skips_unjudged_and_missing_confidence(replay):
    recs = [
        _rec(0.9, "right"),
        _rec(0.9, None),          # unjudged
        _rec(None, "wrong"),      # no confidence
        {"outcome": "right"},     # no confidence key
        _rec("nan-ish", "right"), # non-numeric confidence
    ]
    r = replay.evaluate_threshold(recs, 0.5)
    assert r.total == 1                          # only the first record counts


def test_threshold_at_boundary_is_inclusive(replay):
    # confidence exactly == threshold counts as "acted"
    r = replay.evaluate_threshold([_rec(0.5, "right")], 0.5)
    assert r.kept_right == 1 and r.suppressed_right == 0


# ── sweep + recommendation ────────────────────────────────────────────────────
def _separable_corpus():
    """Right decisions cluster high-confidence, wrong ones low — so a mid
    threshold separates them well."""
    recs = []
    for _ in range(20):
        recs.append(_rec(0.85, "right"))
    for _ in range(20):
        recs.append(_rec(0.25, "wrong"))
    return recs


def test_recommend_picks_separating_threshold(replay):
    rec = replay.recommend_threshold(_separable_corpus(), min_samples=10)
    assert rec is not None
    best = rec["recommended"]
    # a threshold between 0.25 and 0.85 should classify all 40 correctly
    assert 0.25 < best["threshold"] <= 0.85
    assert best["accuracy"] == 1.0
    assert rec["samples"] == 40


def test_recommend_withheld_below_min_samples(replay):
    recs = [_rec(0.9, "right"), _rec(0.2, "wrong")]     # only 2 judged
    assert replay.recommend_threshold(recs, min_samples=25) is None


def test_recommend_tie_breaks_toward_lower_threshold(replay):
    # all right, all high confidence -> every threshold <= 0.9 is 100% accurate;
    # recommendation should prefer the lowest (act more readily)
    recs = [_rec(0.9, "right") for _ in range(30)]
    rec = replay.recommend_threshold(recs, min_samples=10)
    assert rec["recommended"]["threshold"] == 0.0
    assert rec["recommended"]["accuracy"] == 1.0


def test_sweep_covers_grid(replay):
    results = replay.sweep(_separable_corpus())
    assert len(results) == 21                    # 0.00..1.00 step 0.05
    assert all(r.total == 40 for r in results)


# ── DB-facing convenience degrades safely ─────────────────────────────────────
def test_replay_kind_not_ready_with_no_data(replay, monkeypatch):
    import sys
    import types
    fake = types.ModuleType("jc.decision_record")
    fake.recent = lambda **k: []                 # no records
    monkeypatch.setitem(sys.modules, "jc.decision_record", fake)
    monkeypatch.setattr(sys.modules["jc"], "decision_record", fake, raising=False)
    out = replay.replay_kind("intrusion", min_samples=25)
    assert out["ready"] is False
    assert out["samples"] == 0 and out["needed"] == 25


def test_replay_kind_ready_with_enough_judged(replay, monkeypatch):
    import sys
    import types
    fake = types.ModuleType("jc.decision_record")
    fake.recent = lambda **k: _separable_corpus()
    monkeypatch.setitem(sys.modules, "jc.decision_record", fake)
    monkeypatch.setattr(sys.modules["jc"], "decision_record", fake, raising=False)
    out = replay.replay_kind("intrusion", min_samples=10)
    assert out["ready"] is True
    assert out["kind"] == "intrusion"
    assert out["recommended"]["accuracy"] == 1.0
