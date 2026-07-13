"""Tests for the pattern analyzer (v6.26.0) — detectors + knowledge promotion."""
import sqlite3
from datetime import datetime, timedelta

import pytest

_SCHEMA = """
CREATE TABLE state_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL, entity_id TEXT NOT NULL, domain TEXT NOT NULL,
    old_state TEXT, new_state TEXT NOT NULL, area_id TEXT,
    hour INTEGER, day_of_week INTEGER, triggered_by TEXT DEFAULT 'system',
    person TEXT DEFAULT 'unknown'
);
CREATE TABLE commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, text TEXT NOT NULL,
    handled_by TEXT DEFAULT 'agent', entity_ids TEXT DEFAULT '[]',
    person TEXT DEFAULT 'unknown', hour INTEGER, day_of_week INTEGER
);
CREATE TABLE person_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, person TEXT NOT NULL,
    pattern_type TEXT NOT NULL, description TEXT NOT NULL,
    data TEXT DEFAULT '{}', confidence REAL DEFAULT 0.0,
    last_seen TEXT, occurrences INTEGER DEFAULT 1
);
"""


@pytest.fixture
def analyzer(load):
    return load("pattern_analyzer")


@pytest.fixture
def knowledge(load):
    return load("knowledge")


def _conn(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def _ts(days_ago, hour, minute=0):
    dt = (datetime.now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    return dt.isoformat(), dt.weekday()


def _add_state(conn, entity_id, new_state, days_ago, hour, minute=0, person="unknown"):
    ts, dow = _ts(days_ago, hour, minute)
    domain = entity_id.split(".", 1)[0]
    conn.execute(
        "INSERT INTO state_changes (timestamp, entity_id, domain, old_state, "
        "new_state, area_id, hour, day_of_week, person) VALUES (?,?,?,?,?,?,?,?,?)",
        (ts, entity_id, domain, "off", new_state, "", hour, dow, person))


def _add_command(conn, text, days_ago, hour, person="unknown"):
    ts, dow = _ts(days_ago, hour)
    conn.execute(
        "INSERT INTO commands (timestamp, text, hour, day_of_week, person) "
        "VALUES (?,?,?,?,?)",
        (ts, text, hour, dow, person))


def test_time_routine_detected(analyzer, tmp_path):
    conn = _conn(tmp_path / "p.db")
    for d in range(1, 9):  # 8 distinct recent days, porch light on at 18:00
        _add_state(conn, "light.porch_test", "on", d, 18)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = pa._find_time_routines(conn)
    match = [p for p in found if p.entity_ids == ["light.porch_test"]]
    assert match and match[0].details["hour"] == 18
    assert match[0].pattern_type == "time_routine"


def test_repeated_command_detected(analyzer, tmp_path):
    conn = _conn(tmp_path / "p.db")
    for d in range(1, 7):
        _add_command(conn, "goodnight", d, 23)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = pa._find_repeated_commands(conn)
    assert any(p.details.get("command") == "goodnight" for p in found)


def test_sequence_detected(analyzer, tmp_path):
    conn = _conn(tmp_path / "p.db")
    for d in range(1, 7):  # a turns on, b follows 2 min later, same domain
        _add_state(conn, "light.a_test", "on", d, 18, 0)
        _add_state(conn, "light.b_test", "on", d, 18, 2)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = pa._find_sequence_patterns(conn)
    assert any(set(p.entity_ids) == {"light.a_test", "light.b_test"} for p in found)


def test_should_analyze_gates_on_min_days(analyzer, tmp_path):
    db = tmp_path / "p.db"
    conn = _conn(db)
    for d in range(0, 2):  # only ~2 days of data
        _add_state(conn, "light.x", "on", d, 18)
    conn.commit()
    conn.close()
    pa = analyzer.PatternAnalyzer()
    pa._db = str(db)
    assert pa.should_analyze() is False  # < MIN_DAYS


# ── knowledge promotion ──────────────────────────────────────────────────────

def _pattern(analyzer, conf, hour=18, state="on", entity="light.porch_test"):
    return analyzer.DetectedPattern(
        pattern_type="time_routine", description="x", entity_ids=[entity],
        confidence=conf, occurrences=8, details={"hour": hour, "state": state})


def test_promote_writes_observed_fact(analyzer, knowledge, tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "DB_PATH", str(tmp_path / "k.db"))
    pa = analyzer.PatternAnalyzer()
    written = pa._promote_to_knowledge([_pattern(analyzer, 0.9)])
    assert written == 1
    facts = knowledge.all_facts()
    assert len(facts) == 1
    assert facts[0]["source"] == "observed"
    assert "18:00" in facts[0]["value"]


def test_low_confidence_not_promoted(analyzer, knowledge, tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "DB_PATH", str(tmp_path / "k.db"))
    pa = analyzer.PatternAnalyzer()
    assert pa._promote_to_knowledge([_pattern(analyzer, 0.5)]) == 0
    assert knowledge.all_facts() == []


def test_promote_respects_stated_fact(analyzer, knowledge, tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "DB_PATH", str(tmp_path / "k.db"))
    pa = analyzer.PatternAnalyzer()
    p = _pattern(analyzer, 0.9)
    subject, kind, key, _value = pa._fact_for(p)
    # user stated something at this key first
    knowledge.remember(key, "I handle this myself", subject=subject, source="stated")
    pa._promote_to_knowledge([p])
    facts = knowledge.all_facts()
    assert len(facts) == 1
    assert facts[0]["value"] == "I handle this myself"   # not clobbered
    assert facts[0]["source"] == "stated"


def test_respect_stated_allows_observed_update(analyzer, knowledge, tmp_path, monkeypatch):
    # an observed fact CAN be refreshed by re-observation (only stated is protected)
    monkeypatch.setattr(knowledge, "DB_PATH", str(tmp_path / "k.db"))
    pa = analyzer.PatternAnalyzer()
    pa._promote_to_knowledge([_pattern(analyzer, 0.8, hour=18)])
    pa._promote_to_knowledge([_pattern(analyzer, 0.9, hour=19)])  # time shifted
    facts = knowledge.all_facts()
    assert len(facts) == 1                # upserted in place, not duplicated
    assert "19:00" in facts[0]["value"]   # refreshed


# ── per-person attribution (v6.41.0) ─────────────────────────────────────────

def test_time_routine_owned_by_dominant_person(analyzer, tmp_path):
    conn = _conn(tmp_path / "p.db")
    for d in range(1, 9):  # Sam alone accounts for all 8 occurrences
        _add_state(conn, "light.office_test", "on", d, 7, person="Sam")
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = pa._find_time_routines(conn)
    match = [p for p in found if p.entity_ids == ["light.office_test"]]
    assert match and match[0].details.get("person") == "Sam"
    assert "Sam" in match[0].description


def test_time_routine_mixed_people_stays_household(analyzer, tmp_path):
    conn = _conn(tmp_path / "p.db")
    for d in range(1, 5):
        _add_state(conn, "light.hall_test", "on", d, 20, person="Sam")
    for d in range(5, 9):
        _add_state(conn, "light.hall_test", "on", d, 20, person="Alex")
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = pa._find_time_routines(conn)
    match = [p for p in found if p.entity_ids == ["light.hall_test"]]
    assert match and "person" not in match[0].details  # no one dominates


def test_repeated_command_owned_by_dominant_person(analyzer, tmp_path):
    conn = _conn(tmp_path / "p.db")
    for d in range(1, 7):
        _add_command(conn, "play jazz", d, 21, person="Sam")
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = pa._find_repeated_commands(conn)
    match = [p for p in found if p.details.get("command") == "play jazz"]
    assert match and match[0].details.get("person") == "Sam"


def test_dominant_person_missing_column_is_safe(analyzer, tmp_path):
    # An unmigrated DB without the person column must not crash — just no
    # person attribution, same as pre-6.41 household-wide behavior.
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    conn.executescript("""
        CREATE TABLE state_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, entity_id TEXT NOT NULL, domain TEXT NOT NULL,
            old_state TEXT, new_state TEXT NOT NULL, area_id TEXT,
            hour INTEGER, day_of_week INTEGER, triggered_by TEXT DEFAULT 'system'
        );
    """)
    conn.row_factory = sqlite3.Row
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    assert pa._dominant_person(conn, "state_changes", entity="light.x",
                               state="on", hour=8) is None


def test_promote_attributes_fact_to_person_subject(analyzer, knowledge, tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "DB_PATH", str(tmp_path / "k.db"))
    pa = analyzer.PatternAnalyzer()
    p = analyzer.DetectedPattern(
        pattern_type="time_routine", description="x",
        entity_ids=["light.porch_test"], confidence=0.9, occurrences=8,
        details={"hour": 18, "state": "on", "person": "Sam"})
    subject, _kind, _key, _value = pa._fact_for(p)
    assert subject == "sam"
    written = pa._promote_to_knowledge([p])
    assert written == 1
    facts = knowledge.all_facts()
    assert facts[0]["subject"] == "sam"


def test_household_fact_unaffected_when_no_person(analyzer, knowledge, tmp_path, monkeypatch):
    # backward compatibility: no person present → subject stays "household"
    monkeypatch.setattr(knowledge, "DB_PATH", str(tmp_path / "k.db"))
    pa = analyzer.PatternAnalyzer()
    subject, _kind, _key, _value = pa._fact_for(_pattern(analyzer, 0.9))
    assert subject == "household"


def test_store_person_pattern_upserts(analyzer, tmp_path):
    db = tmp_path / "p.db"
    conn = _conn(db)
    conn.commit()
    conn.close()
    pa = analyzer.PatternAnalyzer()
    pa._db = str(db)
    p = analyzer.DetectedPattern(
        pattern_type="time_routine", description="Sam's morning light",
        entity_ids=["light.office_test"], confidence=0.8, occurrences=8,
        details={"hour": 7, "state": "on", "person": "Sam"})
    assert pa._store_person_pattern(p) is True
    rows = pa.get_person_patterns("sam")
    assert len(rows) == 1 and rows[0]["person"] == "sam"

    # re-store with higher confidence → upsert in place, not duplicated
    p2 = analyzer.DetectedPattern(
        pattern_type="time_routine", description="Sam's morning light",
        entity_ids=["light.office_test"], confidence=0.95, occurrences=10,
        details={"hour": 7, "state": "on", "person": "Sam"})
    pa._store_person_pattern(p2)
    rows = pa.get_person_patterns("sam")
    assert len(rows) == 1
    assert rows[0]["occurrences"] == 10


def test_store_person_pattern_no_person_is_noop(analyzer, tmp_path):
    db = tmp_path / "p.db"
    conn = _conn(db)
    conn.commit()
    conn.close()
    pa = analyzer.PatternAnalyzer()
    pa._db = str(db)
    assert pa._store_person_pattern(_pattern(analyzer, 0.9)) is False
    assert pa.get_person_patterns() == []
