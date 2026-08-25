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


def test_suggestion_store_records_decision(load, monkeypatch, tmp_path):
    """Creating a new automation suggestion must log a 'suggestion' decision (Phase 1b)."""
    import sqlite3

    pa = load("pattern_analyzer")
    dr = load("decision_record")
    db = str(tmp_path / "patterns.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, "
        "description TEXT, automation_yaml TEXT, confidence REAL, pattern_count INTEGER, "
        "pattern_type TEXT, entity_ids TEXT, details TEXT, status TEXT)"
    )
    conn.commit()
    conn.close()

    an = pa.PatternAnalyzer()
    an._db = db
    monkeypatch.setattr(an, "_generate_automation", lambda p: "{}")

    captured = {}

    def fake_record(kind, **kw):
        captured["kind"] = kind
        captured.update(kw)
        return 1

    monkeypatch.setattr(dr, "record", fake_record)

    pat = pa.DetectedPattern(
        pattern_type="time_routine",
        description="Porch light at 18:00",
        entity_ids=["light.porch"],
        confidence=0.82,
        occurrences=6,
    )
    assert an._store_suggestion(pat) is True
    assert captured["kind"] == "suggestion"
    assert captured["observation"]["pattern_type"] == "time_routine"
    assert captured["observation"]["entities"] == ["light.porch"]
    assert captured["observation"]["occurrences"] == 6
    assert captured["interpretation"]["suggested"] == "Porch light at 18:00"
    assert abs(captured["confidence"] - 0.82) < 1e-9


def test_suggestion_store_survives_record_failure(load, monkeypatch, tmp_path):
    """A logging failure must NOT flip the store result to False (own try/except)."""
    import sqlite3

    pa = load("pattern_analyzer")
    dr = load("decision_record")
    db = str(tmp_path / "patterns.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, "
        "description TEXT, automation_yaml TEXT, confidence REAL, pattern_count INTEGER, "
        "pattern_type TEXT, entity_ids TEXT, details TEXT, status TEXT)"
    )
    conn.commit()
    conn.close()

    an = pa.PatternAnalyzer()
    an._db = db
    monkeypatch.setattr(an, "_generate_automation", lambda p: "{}")

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(dr, "record", boom)
    pat = pa.DetectedPattern(pattern_type="time_routine", description="X", entity_ids=[], confidence=0.5, occurrences=3)
    assert an._store_suggestion(pat) is True  # still stored despite the logging blow-up


# ── Phase 2: outcome capture ──────────────────────────────────────────────

def test_set_outcome_by_ref_links_and_is_once(load, tmp_path):
    dr = load("decision_record")
    db = str(tmp_path / "d.db")
    rid = dr.record("suggestion", ref="suggestion:11", db_path=db)
    assert dr.set_outcome_by_ref("suggestion:11", "unnecessary", "dismiss_suggestion", db_path=db) is True
    assert dr.get(rid, db_path=db)["outcome"] == "unnecessary"
    assert dr.get(rid, db_path=db)["outcome_source"] == "dismiss_suggestion"
    assert dr.set_outcome_by_ref("suggestion:999", "wrong", db_path=db) is False   # unknown ref
    assert dr.set_outcome_by_ref("suggestion:11", "good", db_path=db) is False      # already judged


def test_set_outcome_recent_picks_recent_unjudged(load, tmp_path):
    import time
    dr = load("decision_record")
    db = str(tmp_path / "d.db")
    old = dr.record("intrusion", ts=time.time() - 10000, db_path=db)
    new = dr.record("intrusion", ts=time.time() - 60, db_path=db)
    assert dr.set_outcome_recent("intrusion", "wrong", "dismiss_intrusion", max_age=3600, db_path=db) is True
    assert dr.get(new, db_path=db)["outcome"] == "wrong"
    assert dr.get(old, db_path=db)["outcome"] is None            # too old, untouched
    assert dr.set_outcome_recent("intrusion", "wrong", max_age=3600, db_path=db) is False  # none left


def _suggestions_db(tmp_path, sid, status="pending"):
    import sqlite3
    db = str(tmp_path / "p.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE suggestions (id INTEGER PRIMARY KEY, status TEXT, dismissed_at TEXT, approved_at TEXT)")
    conn.execute("INSERT INTO suggestions (id, status) VALUES (?, ?)", (sid, status))
    conn.commit()
    conn.close()
    return db


def test_dismiss_suggestion_marks_unnecessary(load, monkeypatch, tmp_path):
    pa = load("pattern_analyzer")
    dr = load("decision_record")
    an = pa.PatternAnalyzer()
    an._db = _suggestions_db(tmp_path, 7)
    calls = []
    monkeypatch.setattr(dr, "set_outcome_by_ref",
                        lambda ref, verdict, source="": calls.append((ref, verdict, source)) or True)
    an.dismiss_suggestion(7)
    assert ("suggestion:7", "unnecessary", "dismiss_suggestion") in calls


def test_mark_installed_marks_good(load, monkeypatch, tmp_path):
    pa = load("pattern_analyzer")
    dr = load("decision_record")
    an = pa.PatternAnalyzer()
    an._db = _suggestions_db(tmp_path, 9)
    calls = []
    monkeypatch.setattr(dr, "set_outcome_by_ref",
                        lambda ref, verdict, source="": calls.append((ref, verdict, source)) or True)
    an.mark_installed(9, "automation.foo")
    assert ("suggestion:9", "good", "installed") in calls


def test_dismiss_intrusion_marks_wrong(load, monkeypatch):
    intr = load("intrusion")
    dr = load("decision_record")
    calls = []
    monkeypatch.setattr(dr, "set_outcome_recent",
                        lambda kind, verdict, source="", max_age=3600.0: calls.append((kind, verdict, source)) or True)
    try:
        intr.dismiss_intrusion("that's me")
        assert ("intrusion", "wrong", "dismiss_intrusion") in calls
    finally:
        # dismiss_intrusion sets a module-global call-off window; clearing it keeps this
        # from leaking into other intrusion tests (shared jc.intrusion module).
        intr.clear_calloff()
        intr._called_off_until = 0.0
