"""Tests for feedback.py — the generalized adaptive-threshold engine that closes
the LEARN loop from Decision Record outcomes."""
import pytest


@pytest.fixture
def fb(load):
    m = load("feedback")
    m.reset_cache()
    return m


@pytest.fixture
def dr(load, tmp_path):
    m = load("decision_record")
    return m, str(tmp_path / "dr.db")


def _seed(dr_mod, db, kind, good=0, unnecessary=0, wrong=0):
    for _ in range(good):
        rid = dr_mod.record(kind, decision="x", db_path=db)
        dr_mod.set_outcome(rid, "good", db_path=db)
    for _ in range(unnecessary):
        rid = dr_mod.record(kind, decision="x", db_path=db)
        dr_mod.set_outcome(rid, "unnecessary", db_path=db)
    for _ in range(wrong):
        rid = dr_mod.record(kind, decision="x", db_path=db)
        dr_mod.set_outcome(rid, "wrong", db_path=db)


def test_no_optin_is_a_noop(fb, dr, monkeypatch):
    dr_mod, db = dr
    _seed(dr_mod, db, "anticipation", wrong=10)
    # opt_in_key given but config off → 0.0 delta
    import types, sys
    fake_cfg = types.SimpleNamespace(get=lambda k, d=None: False)
    monkeypatch.setitem(sys.modules, "jc.jarvis_config", fake_cfg)
    assert fb.threshold_delta("anticipation", opt_in_key="x", db_path=db) == 0.0


def test_mostly_unwelcome_raises_bar(fb, dr):
    dr_mod, db = dr
    _seed(dr_mod, db, "anticipation", good=1, unnecessary=5, wrong=4)  # 90% unwelcome
    d = fb.threshold_delta("anticipation", db_path=db)   # no opt_in_key → always on
    assert d == 0.15


def test_mostly_welcome_relaxes_bar(fb, dr):
    dr_mod, db = dr
    _seed(dr_mod, db, "anticipation", good=10)           # 0% unwelcome
    assert fb.threshold_delta("anticipation", db_path=db) == -0.07


def test_too_little_evidence_is_a_noop(fb, dr):
    dr_mod, db = dr
    _seed(dr_mod, db, "anticipation", wrong=2)           # below min_judged (5)
    assert fb.threshold_delta("anticipation", db_path=db) == 0.0


def test_effective_threshold_clamps(fb, dr):
    dr_mod, db = dr
    _seed(dr_mod, db, "anticipation", good=1, unnecessary=5, wrong=4)   # +0.15
    assert fb.effective_threshold(0.9, "anticipation", hi=0.95, db_path=db) == pytest.approx(0.95)
    fb.reset_cache()
    _seed(dr_mod, db, "other", good=10)                                 # -0.07
    assert fb.effective_threshold(0.32, "other", lo=0.3, db_path=db) == pytest.approx(0.3)
