"""Calibration readout + interruption budget over decision-record outcomes.

calibration() compares stated confidence to actual correctness (good=1, else 0);
interruption_budget() derives a cap multiplier from how often recent proactive
decisions were judged unwelcome. Both are pure DB reads against a temp store.
"""
import pytest


@pytest.fixture
def dr(load):
    return load("decision_record")


def _seed(dr, db, entries):
    dr.ensure_schema(db)
    for conf, verdict in entries:
        rid = dr.record("intrusion", confidence=conf, db_path=db)
        assert rid is not None
        if verdict:
            assert dr.set_outcome(rid, verdict, source="test", db_path=db) is True


def test_calibration_well_calibrated(dr, tmp_path):
    db = str(tmp_path / "d.db")
    _seed(dr, db, [(0.9, "good"), (0.9, "good"), (0.1, "wrong"), (0.1, "wrong")])
    cal = dr.calibration(db_path=db, bins=5)
    assert cal["n"] == 4
    assert cal["good_rate"] == 0.5
    assert cal["brier"] < 0.05           # confidence tracked reality
    bins = {(b["lo"], b["hi"]): b for b in cal["bins"]}
    assert bins[(0.8, 1.0)]["good_rate"] == 1.0
    assert bins[(0.0, 0.2)]["good_rate"] == 0.0


def test_calibration_overconfident_high_brier(dr, tmp_path):
    db = str(tmp_path / "d.db")
    _seed(dr, db, [(0.9, "wrong"), (0.9, "wrong"), (0.9, "wrong")])
    cal = dr.calibration(db_path=db)
    assert cal["good_rate"] == 0.0
    assert cal["brier"] > 0.7            # (0.9-0)^2 = 0.81


def test_calibration_unnecessary_counts_as_not_good(dr, tmp_path):
    db = str(tmp_path / "d.db")
    _seed(dr, db, [(0.5, "unnecessary"), (0.5, "good")])
    cal = dr.calibration(db_path=db)
    assert cal["good_rate"] == 0.5       # unnecessary is not 'good'


def test_calibration_empty_store(dr, tmp_path):
    db = str(tmp_path / "d.db")
    dr.ensure_schema(db)
    cal = dr.calibration(db_path=db)
    assert cal["n"] == 0 and cal["brier"] is None and cal["bins"] == []


def test_interruption_budget_healthy_vs_over(dr, tmp_path):
    db = str(tmp_path / "d.db")
    _seed(dr, db, [(0.8, "good")] * 4 + [(0.8, "unnecessary")])
    b = dr.interruption_budget(db_path=db)
    assert b["judged"] == 5 and b["unwelcome_rate"] == 0.2
    assert b["assessment"] == "healthy" and b["multiplier"] < 1.0


def test_interruption_budget_over_interrupting_tightens(dr, tmp_path):
    db = str(tmp_path / "d.db")
    _seed(dr, db, [(0.8, "wrong"), (0.8, "unnecessary"), (0.8, "unnecessary"), (0.8, "good")])
    b = dr.interruption_budget(db_path=db, floor=0.25)
    assert b["unwelcome_rate"] == 0.75
    assert b["assessment"] == "over-interrupting"
    assert b["multiplier"] <= 0.5        # pulled toward the floor


def test_interruption_budget_no_data_is_neutral(dr, tmp_path):
    db = str(tmp_path / "d.db")
    dr.ensure_schema(db)
    b = dr.interruption_budget(db_path=db)
    assert b["multiplier"] == 1.0 and b["assessment"] == "no data"
