"""Pattern quality: coverage + negative evidence — v7.45.0.

A time routine is scored by how many days it actually happened out of how many
it could have (coverage), not by a raw hit count. A routine that fires on most
opportunity days outranks one that fires on few, even with the same hit count,
and the missed days are surfaced as evidence.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest


_SCHEMA = """
CREATE TABLE state_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, entity_id TEXT,
    domain TEXT, old_state TEXT, new_state TEXT, area_id TEXT,
    person TEXT DEFAULT 'unknown', hour INTEGER, day_of_week INTEGER
);
"""


@pytest.fixture
def analyzer(load):
    return load("pattern_analyzer")


def _conn(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def _add(conn, entity, days_ago, hour, minute=0, state="on"):
    dt = (datetime.now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    conn.execute(
        "INSERT INTO state_changes (timestamp, entity_id, domain, old_state, "
        "new_state, area_id, hour, day_of_week, person) VALUES (?,?,?,?,?,?,?,?,?)",
        (dt.isoformat(), entity, entity.split(".")[0], "off", state, "",
         hour, dt.weekday(), "unknown"))


def test_coverage_and_negative_evidence_in_details(analyzer, tmp_path):
    conn = _conn(tmp_path / "p.db")
    # light.a: every one of 6 observed days at 18:00
    for d in range(1, 7):
        _add(conn, "light.a", d, 18)
    # light.b: only 3 of those days, but twice each (6 hits -> passes the count
    # gate, yet on just 3 distinct days -> coverage 0.5, missed 3)
    for d in range(1, 4):
        _add(conn, "light.b", d, 18, minute=0)
        _add(conn, "light.b", d, 18, minute=30)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = {p.entity_ids[0]: p for p in pa._find_time_routines(conn)}

    a, b = found["light.a"], found["light.b"]
    # opportunity window = 6 distinct days (from light.a)
    assert a.details["observed_days"] == 6 and a.details["opportunity_days"] == 6
    assert a.details["skipped_days"] == 0
    assert a.coverage == 1.0

    assert b.details["observed_days"] == 3 and b.details["opportunity_days"] == 6
    assert b.details["skipped_days"] == 3
    assert b.coverage == 0.5


def test_higher_coverage_scores_higher(analyzer, tmp_path):
    conn = _conn(tmp_path / "p.db")
    for d in range(1, 7):
        _add(conn, "light.a", d, 18)          # 6 of 6 days
    for d in range(1, 4):
        _add(conn, "light.b", d, 18, minute=0)
        _add(conn, "light.b", d, 18, minute=30)   # 3 of 6 days
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = {p.entity_ids[0]: p for p in pa._find_time_routines(conn)}
    # same 6 hits each, but the full-coverage routine is more confident
    assert found["light.a"].occurrences == found["light.b"].occurrences == 6
    assert found["light.a"].confidence > found["light.b"].confidence


def test_small_sample_is_discounted(analyzer, tmp_path):
    conn = _conn(tmp_path / "p.db")
    # 2 hits/day on 3 days => count 6 (passes gate), coverage 1.0, but only 3
    # positive days < MIN_OCCURRENCES(5) => confidence discounted below 1.0
    for d in range(1, 4):
        _add(conn, "light.c", d, 7, minute=0)
        _add(conn, "light.c", d, 7, minute=30)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    match = [p for p in pa._find_time_routines(conn) if p.entity_ids == ["light.c"]]
    assert match
    p = match[0]
    assert p.coverage == 1.0
    assert p.confidence < 1.0          # small sample -> not fully trusted
    assert abs(p.confidence - round(3 / analyzer.MIN_OCCURRENCES, 3)) < 1e-6


def test_explain_surfaces_missed_days(analyzer):
    out = analyzer.explain_suggestion(
        "time_routine",
        {"hour": 7, "state": "on", "coverage": 0.7,
         "observed_days": 42, "opportunity_days": 60},
        count=50)
    joined = " | ".join(out["evidence"])
    assert "42 of 60 days" in joined
    assert "missed 18" in joined


def test_explain_falls_back_without_coverage_fields(analyzer):
    # older stored suggestions have no observed/opportunity days
    out = analyzer.explain_suggestion(
        "time_routine", {"hour": 18, "state": "off", "consistency": 0.8}, count=12)
    joined = " | ".join(out["evidence"])
    assert "12 times" in joined
    assert "80% of days" in joined
