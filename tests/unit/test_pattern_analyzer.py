"""Tests for the pattern analyzer (v6.26.0) — detectors + knowledge promotion."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
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
    conn.close()


def test_repeated_command_detected(analyzer, tmp_path):
    conn = _conn(tmp_path / "p.db")
    for d in range(1, 7):
        _add_command(conn, "goodnight", d, 23)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = pa._find_repeated_commands(conn)
    assert any(p.details.get("command") == "goodnight" for p in found)
    conn.close()


def test_sequence_detected(analyzer, tmp_path):
    conn = _conn(tmp_path / "p.db")
    for d in range(1, 7):  # a turns on, b follows 2 min later, same domain
        _add_state(conn, "light.a_test", "on", d, 18, 0)
        _add_state(conn, "light.b_test", "on", d, 18, 2)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = pa._find_sequence_patterns(conn)
    assert any(set(p.entity_ids) == {"light.a_test", "light.b_test"} for p in found)
    conn.close()


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


def _seed_enough(analyzer, tmp_path, name):
    db = tmp_path / name
    conn = _conn(db)
    for d in range(0, 9):          # 9 distinct days (oldest 8d ago >= MIN_DAYS)
        for h in range(6, 14):     # 8 rows/day -> 72 total >= 50
            _add_state(conn, "light.x", "on" if h % 2 else "off", d, h)
    conn.commit()
    conn.close()
    pa = analyzer.PatternAnalyzer()
    pa._db = str(db)
    return pa


def test_should_analyze_throttle_blocks_recent(analyzer, tmp_path):
    import time as _t
    pa = _seed_enough(analyzer, tmp_path, "t1.db")
    pa._last_analysis = _t.time()          # just analyzed
    assert pa.should_analyze() is False    # 6h throttle blocks


def test_reset_last_analysis_reenables(analyzer, tmp_path):
    import time as _t
    pa = _seed_enough(analyzer, tmp_path, "t2.db")
    pa._last_analysis = _t.time()
    assert pa.should_analyze() is False
    pa._last_analysis = 0.0                 # the manual-run bypass
    assert pa.should_analyze() is True


def test_data_gate_holds_even_after_reset(analyzer, tmp_path):
    # bypassing the throttle must NOT bypass the data gate — no noise on a fresh install
    db = tmp_path / "t3.db"
    conn = _conn(db)
    for d in range(0, 2):
        _add_state(conn, "light.x", "on", d, 18)
    conn.commit()
    conn.close()
    pa = analyzer.PatternAnalyzer()
    pa._db = str(db)
    pa._last_analysis = 0.0
    assert pa.should_analyze() is False


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
    conn.close()


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
    conn.close()


def test_repeated_command_owned_by_dominant_person(analyzer, tmp_path):
    conn = _conn(tmp_path / "p.db")
    for d in range(1, 7):
        _add_command(conn, "play jazz", d, 21, person="Sam")
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = pa._find_repeated_commands(conn)
    match = [p for p in found if p.details.get("command") == "play jazz"]
    assert match and match[0].details.get("person") == "Sam"
    conn.close()


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
    conn.close()


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


# ── cross-thread connection contract ─────────────────────────────────────────
# FakeHass.async_add_executor_job runs synchronously on the caller's thread
# (tests/fakes.py), so the analyze() integration tests never actually hand the
# connection to a different OS thread and would still pass even without
# check_same_thread=False. This test uses a REAL ThreadPoolExecutor to open the
# connection on this (main) thread and run a finder on a genuinely different
# worker thread, the same handoff analyze() performs against real HA.

def test_run_all_finders_on_real_worker_thread(analyzer, tmp_path, monkeypatch):
    db = tmp_path / "p.db"
    conn = _conn(db)
    for d in range(1, 9):
        _add_state(conn, "light.porch_test", "on", d, 18)
    conn.commit()
    conn.close()

    pa = analyzer.PatternAnalyzer()
    pa._db = str(db)
    opened = []
    original_connect = pa._connect

    def capture_connection():
        conn = original_connect()
        opened.append(conn)
        return conn

    monkeypatch.setattr(pa, "_connect", capture_connection)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(pa._run_all_finders, {}, None, None, {})
        patterns = future.result()  # re-raises any sqlite3.ProgrammingError

    assert any(p.entity_ids == ["light.porch_test"] for p in patterns)
    # _run_all_finders is documented to close the connection itself.
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_event_domain_not_learned_as_routine(analyzer, tmp_path):
    """#560: an HA automatic backup (event.*) firing at the same time daily is
    not a household routine — it must not be learned or pushed, while a real
    routine alongside it still is."""
    conn = _conn(tmp_path / "noise.db")
    for d in range(1, 9):
        _add_state(conn, "event.backup_backup_automatico", "in_progress", d, 5)
        _add_state(conn, "light.porch_test", "on", d, 18)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = pa._find_time_routines(conn)
    ents = [e for p in found for e in p.entity_ids]
    assert "event.backup_backup_automatico" not in ents
    assert "light.porch_test" in ents
    conn.close()


# ── §7E: median minute, controllable priority, exclusions, state hygiene ──────

def test_time_routine_median_minute(analyzer, tmp_path):
    """The routine fires at the learned median minute (rounded to 5), not always
    the top of the hour, and _generate_automation emits HH:MM:00."""
    import json
    conn = _conn(tmp_path / "min.db")
    for d in range(1, 9):                       # porch light ~22:45 every night
        _add_state(conn, "light.porch_test", "on", d, 22, minute=44 if d % 2 else 46)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    m = [p for p in pa._find_time_routines(conn)
         if p.entity_ids == ["light.porch_test"]][0]
    assert m.details["hour"] == 22
    assert m.details["minute"] == 45            # median(44,46) = 45, nearest 5
    auto = json.loads(pa._generate_automation(m))
    assert auto["trigger"]["at"] == "22:45:00"
    conn.close()


def test_median_minute_helper(analyzer, tmp_path):
    conn = _conn(tmp_path / "mm.db")
    for d in range(1, 7):
        _add_state(conn, "light.x_test", "on", d, 9, minute=12)
        _add_state(conn, "light.x_test", "on", d, 9, minute=18)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    assert pa._median_minute(conn, "light.x_test", "on", 9) == 15   # median(12,18)
    assert pa._median_minute(conn, "light.none_test", "on", 9) == 0  # no rows → :00
    conn.close()


def test_time_routine_controllable_not_starved_by_readonly(analyzer, tmp_path):
    """25 read-only sensors firing twice a day (higher raw count) must not evict
    the 3 real controllable routines from the [:20] cap."""
    conn = _conn(tmp_path / "starve.db")
    for i in range(25):
        for d in range(1, 9):
            _add_state(conn, f"binary_sensor.ro_{i}", "on", d, i % 24, minute=10)
            _add_state(conn, f"binary_sensor.ro_{i}", "on", d, i % 24, minute=50)
    for j, h in enumerate((6, 12, 21)):
        for d in range(1, 9):
            _add_state(conn, f"light.ctrl_{j}", "on", d, h)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = pa._find_time_routines(conn)
    ents = {e for p in found for e in p.entity_ids}
    assert len(found) == 20                     # cap respected
    for j in range(3):
        assert f"light.ctrl_{j}" in ents, "a controllable routine was starved"
    conn.close()


def test_time_routine_skips_excluded(analyzer, tmp_path):
    conn = _conn(tmp_path / "excl.db")
    for d in range(1, 9):
        _add_state(conn, "light.porch_test", "on", d, 18)
        _add_state(conn, "light.noisy_test", "on", d, 7)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = pa._find_time_routines(conn, excluded={"light.noisy_test"})
    ents = {e for p in found for e in p.entity_ids}
    assert "light.porch_test" in ents
    assert "light.noisy_test" not in ents
    conn.close()


def test_time_routine_ignores_unavailable_state(analyzer, tmp_path):
    """A device dropping to unavailable/unknown at a regular time is not a
    schedulable routine — it is filtered in SQL before it can look like one."""
    conn = _conn(tmp_path / "unavail.db")
    for d in range(1, 9):
        _add_state(conn, "light.flappy_test", "unavailable", d, 3)
        _add_state(conn, "light.porch_test", "on", d, 18)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    found = pa._find_time_routines(conn)
    ents = {e for p in found for e in p.entity_ids}
    assert "light.porch_test" in ents
    assert "light.flappy_test" not in ents
    conn.close()


# ── §7H-1: ISO timestamp window bounds ───────────────────────────────────────

def test_recent_cutoff_iso_is_t_separated(analyzer):
    """The window bound uses JARVIS's own ISO 'T' separator, not SQLite's space,
    so `timestamp > ?` compares in the same lexicographic space as stored rows."""
    c = analyzer._recent_cutoff_iso(30)
    assert "T" in c and " " not in c
    days = (datetime.now() - datetime.fromisoformat(c)).total_seconds() / 86400
    assert 29.9 < days < 30.1


def test_time_routine_window_excludes_old_includes_recent(analyzer, tmp_path):
    """A routine whose occurrences are all >30 days old is outside the window and
    not learned; a recent one is — proving the ISO bound filters correctly."""
    conn = _conn(tmp_path / "cut.db")
    for d in range(40, 48):          # 40–47 days ago → outside the 30-day window
        _add_state(conn, "light.old_test", "on", d, 18)
    for d in range(5, 13):           # 5–12 days ago → inside the window
        _add_state(conn, "light.new_test", "on", d, 18)
    conn.commit()
    pa = analyzer.PatternAnalyzer()
    ents = {e for p in pa._find_time_routines(conn) for e in p.entity_ids}
    assert "light.new_test" in ents
    assert "light.old_test" not in ents
    conn.close()
