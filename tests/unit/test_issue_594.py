"""Issue #594 — false person routines from flapping network device_trackers.

A reporter's panel filled with bogus per-person routines because a router-backed
``device_tracker`` flapped home/away dozens of times an hour. Three guards:

1. ``_find_time_routines`` discards an entity whose same-hour events-per-day far
   exceed any plausible once-a-day anchor (the flap gate).
2. ``_dominant_person`` no longer lets a tiny named sliver win an owner when the
   unattributed ``unknown`` bucket outweighs it — the pattern stays household-wide.
3. ``person_patterns.delete`` gives the panel a supported way to forget a routine
   that was learned from noise.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest


# --- pattern_analyzer fixtures ------------------------------------------------

_TIME_SCHEMA = """
CREATE TABLE state_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, entity_id TEXT,
    domain TEXT, old_state TEXT, new_state TEXT, area_id TEXT,
    person TEXT DEFAULT 'unknown', hour INTEGER, day_of_week INTEGER
);
"""

_PERSON_SCHEMA = """
CREATE TABLE state_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, entity_id TEXT,
    new_state TEXT, person TEXT, person_confidence REAL, hour INTEGER
);
"""


@pytest.fixture
def analyzer(load):
    return load("pattern_analyzer")


@pytest.fixture
def pp(load):
    load("identity")            # so _normalize resolves
    return load("person_patterns")


def _time_conn(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(_TIME_SCHEMA)
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def _person_conn(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(_PERSON_SCHEMA)
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


def _sc(conn, entity, state, hour, person, conf, n, days_ago=1):
    dt = (datetime.now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0)
    for _ in range(n):
        conn.execute(
            "INSERT INTO state_changes (timestamp, entity_id, new_state, person, "
            "person_confidence, hour) VALUES (?,?,?,?,?,?)",
            (dt.isoformat(), entity, state, person, conf, hour))


# --- 1. flap gate -------------------------------------------------------------

def test_flapping_tracker_is_not_a_routine(analyzer, tmp_path):
    conn = _time_conn(tmp_path / "p.db")
    # device_tracker.router flaps ~10x in hour 8 on each of 8 days: same-hour
    # count 80 over 8 positive days = 10/day >> _FLAP_EVENTS_PER_DAY(8) -> dropped,
    # even though its coverage would otherwise be a perfect 8-of-8.
    for d in range(1, 9):
        for m in range(10):
            _add(conn, "device_tracker.router", d, 8, minute=m, state="home")
    # light.kitchen: a genuine once-a-day routine at hour 7 on each of 8 days.
    for d in range(1, 9):
        _add(conn, "light.kitchen", d, 7, state="on")
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = {p.entity_ids[0] for p in pa._find_time_routines(conn)}
    assert "device_tracker.router" not in found    # flap gate drops the noise
    assert "light.kitchen" in found                 # genuine routine survives
    conn.close()


def test_once_a_day_tracker_survives_the_flap_gate(analyzer, tmp_path):
    conn = _time_conn(tmp_path / "p.db")
    # A real presence routine: home at hour 18 once per day on 8 days. 8/8 -> the
    # flap gate (events-per-day == 1) must not touch it.
    for d in range(1, 9):
        _add(conn, "device_tracker.phone", d, 18, state="home")
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = {p.entity_ids[0] for p in pa._find_time_routines(conn)}
    assert "device_tracker.phone" in found
    conn.close()


# --- 2. _dominant_person unknown-weight guard --------------------------------

def test_named_sliver_beside_unknown_stays_household(analyzer, tmp_path):
    conn = _person_conn(tmp_path / "s.db")
    # 10 'unknown' (wt 5.0) vs 6 'sindy' (wt 3.0). Sindy is the only named person,
    # so her share of NAMED weight is 100% (>= 0.8) and her count (6) clears
    # MIN_OCCURRENCES -- the ONLY thing that can reject her is the #594 guard,
    # because the unattributed bucket outweighs her. Pattern stays household-wide.
    _sc(conn, "light.hall", "on", 20, "unknown", 0.5, 10)
    _sc(conn, "light.hall", "on", 20, "sindy", 0.5, 6)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    assert pa._dominant_person(conn, "state_changes", hour=20,
                               entity="light.hall", state="on") is None
    conn.close()


def test_true_owner_still_resolves(analyzer, tmp_path):
    conn = _person_conn(tmp_path / "s.db")
    # 10 'sam' (wt 5.0) vs 2 'unknown' (wt 1.0): sam dominates the named weight
    # AND outweighs the unknown bucket -> the owner still resolves to 'sam'.
    _sc(conn, "light.hall", "on", 7, "sam", 0.5, 10)
    _sc(conn, "light.hall", "on", 7, "unknown", 0.5, 2)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    assert pa._dominant_person(conn, "state_changes", hour=7,
                               entity="light.hall", state="on") == "sam"
    conn.close()


def test_owner_resolves_when_no_unknown_present(analyzer, tmp_path):
    conn = _person_conn(tmp_path / "s.db")
    # No unknown bucket at all: a clean sole-occupant routine still resolves.
    _sc(conn, "switch.coffee", "on", 6, "sam", 0.5, 9)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    assert pa._dominant_person(conn, "state_changes", hour=6,
                               entity="switch.coffee", state="on") == "sam"
    conn.close()


# --- 3. person_patterns.delete -----------------------------------------------

def test_delete_removes_one_routine_by_id(pp, tmp_path):
    db = str(tmp_path / "p.db")
    pp.store("sam", "time_routine", "a", db_path=db)
    pp.store("sam", "time_routine", "b", db_path=db)
    rows = pp.read(db_path=db)
    assert len(rows) == 2
    target = rows[0]["id"]
    assert pp.delete(target, db_path=db) is True        # a row was removed
    remaining = pp.read(db_path=db)
    assert len(remaining) == 1
    assert all(r["id"] != target for r in remaining)    # the right one went
    assert pp.delete(target, db_path=db) is False       # already gone -> False


def test_delete_absent_id_is_false(pp, tmp_path):
    db = str(tmp_path / "p.db")
    pp.store("sam", "time_routine", "a", db_path=db)
    assert pp.delete(99999, db_path=db) is False         # never raises
    assert len(pp.read(db_path=db)) == 1                 # untouched


def test_delete_missing_db_is_false(pp, tmp_path):
    assert pp.delete(1, db_path=str(tmp_path / "nope.db")) is False
