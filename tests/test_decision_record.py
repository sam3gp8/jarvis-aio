"""Decision Record (v7.32.0) — immutable per-decision record with a single outcome."""

import os
import tempfile

def _tmp():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # let the module create it
    return path


def test_record_and_get_round_trips_granular_split(load):
    dr = load("decision_record")
    db = _tmp()
    try:
        rid = dr.record(
            "anticipation_departure",
            observation={"event": "Gym", "mins_to": 20, "presence": "home"},
            interpretation={"predicted": "leaving soon", "pattern": "gym_mon"},
            evidence={"travel_min": 12},
            decision="announce departure heads-up",
            reason="recurring event + travel time",
            confidence=0.72,
            db_path=db,
        )
        assert isinstance(rid, int) and rid > 0
        rec = dr.get(rid, db_path=db)
        assert rec is not None
        # observation and interpretation stay separate structured objects
        assert rec["observation"] == {"event": "Gym", "mins_to": 20, "presence": "home"}
        assert rec["interpretation"]["predicted"] == "leaving soon"
        assert rec["evidence"]["travel_min"] == 12
        assert rec["decision"] == "announce departure heads-up"
        assert abs(rec["confidence"] - 0.72) < 1e-9
        # deterministic predictor -> no model metadata
        assert rec["model"] is None and rec["tokens"] is None
        # unjudged by default
        assert rec["outcome"] is None
    finally:
        os.path.exists(db) and os.unlink(db)


def test_outcome_is_set_once_then_immutable(load):
    dr = load("decision_record")
    db = _tmp()
    try:
        rid = dr.record("intrusion", decision="alert", reason="door open while away", db_path=db)
        assert dr.set_outcome(rid, dr.OUTCOME_WRONG, source="dismiss_intrusion", db_path=db) is True
        rec = dr.get(rid, db_path=db)
        assert rec["outcome"] == "wrong" and rec["outcome_source"] == "dismiss_intrusion"
        # a second attempt must NOT overwrite — the record is immutable once judged
        assert dr.set_outcome(rid, dr.OUTCOME_GOOD, source="oops", db_path=db) is False
        assert dr.get(rid, db_path=db)["outcome"] == "wrong"
    finally:
        os.path.exists(db) and os.unlink(db)


def test_set_outcome_missing_record_is_false(load):
    dr = load("decision_record")
    db = _tmp()
    try:
        dr.record("suggestion", db_path=db)
        assert dr.set_outcome(99999, dr.OUTCOME_UNNECESSARY, db_path=db) is False
        assert dr.set_outcome(None, dr.OUTCOME_WRONG, db_path=db) is False
    finally:
        os.path.exists(db) and os.unlink(db)


def test_recent_filters_by_kind_and_unjudged(load):
    dr = load("decision_record")
    db = _tmp()
    try:
        a = dr.record("anticipation_departure", ts=100.0, db_path=db)
        b = dr.record("intrusion", ts=200.0, db_path=db)
        dr.record("anticipation_departure", ts=300.0, db_path=db)
        dr.set_outcome(a, dr.OUTCOME_GOOD, db_path=db)
        # newest first
        assert [r["kind"] for r in dr.recent(db_path=db)][0] == "anticipation_departure"
        # by kind
        assert all(r["kind"] == "intrusion" for r in dr.recent(kind="intrusion", db_path=db))
        assert dr.recent(kind="intrusion", db_path=db)[0]["id"] == b
        # only-unjudged excludes the one we judged
        unjudged_ids = {r["id"] for r in dr.recent(only_unjudged=True, db_path=db)}
        assert a not in unjudged_ids
    finally:
        os.path.exists(db) and os.unlink(db)


def test_stats_counts_by_outcome(load):
    dr = load("decision_record")
    db = _tmp()
    try:
        r1 = dr.record("suggestion", db_path=db)
        r2 = dr.record("suggestion", db_path=db)
        dr.record("intrusion", db_path=db)  # left unjudged
        dr.set_outcome(r1, dr.OUTCOME_GOOD, db_path=db)
        dr.set_outcome(r2, dr.OUTCOME_UNNECESSARY, db_path=db)
        s = dr.stats(db_path=db)
        assert s["total"] == 3 and s["judged"] == 2
        assert s["good"] == 1 and s["unnecessary"] == 1 and s["wrong"] == 0
    finally:
        os.path.exists(db) and os.unlink(db)


def test_record_failure_is_soft_returns_none(load, tmp_path):
    dr = load("decision_record")
    # a FILE where a directory is expected -> connect fails -> best-effort returns None (no raise)
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    assert dr.record("intrusion", db_path=str(blocker / "decisions.db")) is None


def test_cognition_log_decision_forwards_granular_split(load, monkeypatch):
    """The anticipation wiring must pass observation/interpretation through intact."""
    cog = load("cognition")
    dr = load("decision_record")
    captured = {}

    def fake_record(kind, **kw):
        captured["kind"] = kind
        captured.update(kw)
        return 1

    monkeypatch.setattr(dr, "record", fake_record)
    cog._log_decision(
        "anticipation_departure",
        {"event": "Gym", "minutes_until": 20},
        {"predicted": "leave soon"},
        "announce departure heads-up",
        "recurring calendar event",
        confidence=0.7,
    )
    assert captured["kind"] == "anticipation_departure"
    assert captured["observation"] == {"event": "Gym", "minutes_until": 20}
    assert captured["interpretation"] == {"predicted": "leave soon"}
    assert captured["decision"] == "announce departure heads-up"
    assert abs(captured["confidence"] - 0.7) < 1e-9


def test_cognition_log_decision_never_raises(load, monkeypatch):
    cog = load("cognition")
    dr = load("decision_record")

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(dr, "record", boom)
    # must swallow — logging a decision can never break the decision
    cog._log_decision("anticipation_overdue", {}, {}, "x", "y")
