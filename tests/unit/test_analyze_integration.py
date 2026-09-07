"""Integration tests for the async analyze() assembly.

Unit tests cover each detector in isolation; these drive the full
PatternAnalyzer.analyze(hass) orchestration with a fake hass, so a wiring
regression — a detector dropped from analyze(), or the sensor-history fetch
breaking the sequence/numeric detectors it now feeds — is caught. The store
methods are monkeypatched so we assert on what analyze() *produces*, not on
downstream persistence (covered elsewhere).
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

_SCHEMA = """
CREATE TABLE state_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
    entity_id TEXT NOT NULL, domain TEXT NOT NULL, old_state TEXT,
    new_state TEXT NOT NULL, area_id TEXT, hour INTEGER, day_of_week INTEGER,
    triggered_by TEXT DEFAULT 'system', person TEXT DEFAULT 'unknown'
);
CREATE INDEX idx_sc_ts ON state_changes(timestamp);
CREATE TABLE commands (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
    command TEXT, entity_id TEXT, hour INTEGER, day_of_week INTEGER,
    person TEXT DEFAULT 'unknown');
CREATE TABLE person_patterns (id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT, pattern_type TEXT, description TEXT, entity_ids TEXT,
    confidence REAL, details TEXT, created TEXT, last_seen TEXT);
"""


@pytest.fixture
def pa(load):
    return load("pattern_analyzer")


def _ins(conn, eid, st, when):
    conn.execute(
        "INSERT INTO state_changes (timestamp, entity_id, domain, old_state, "
        "new_state, area_id, hour, day_of_week, person) VALUES (?,?,?,?,?,?,?,?,?)",
        (when.isoformat(), eid, eid.split(".")[0], "off", st, "",
         when.hour, when.weekday(), "unknown"))


def _capture(pa, db, monkeypatch):
    an = pa.PatternAnalyzer()
    an._db = db
    stored = []
    monkeypatch.setattr(an, "_store_suggestion",
                        lambda p: (stored.append(p), True)[1])
    monkeypatch.setattr(an, "_store_person_pattern", lambda p: False)
    monkeypatch.setattr(an, "_promote_to_knowledge", lambda pats: 0)
    monkeypatch.setattr(pa, "_learned_threshold_delta", lambda: 0.0)
    return an, stored


async def test_analyze_runs_time_routine_and_sequence(pa, tmp_path, monkeypatch, fake_hass):
    db = str(tmp_path / "a.db")
    conn = sqlite3.connect(db); conn.executescript(_SCHEMA)
    base = datetime.now() - timedelta(days=14)
    for d in range(12):
        day = base + timedelta(days=d)
        _ins(conn, "light.porch", "on", day.replace(hour=18, minute=0))       # daily routine
        t = day.replace(hour=20, minute=0, second=17)                          # a sequence
        _ins(conn, "light.hall", "on", t)
        _ins(conn, "light.kitchen", "on", t + timedelta(seconds=45))
    conn.commit(); conn.close()

    an, stored = _capture(pa, db, monkeypatch)
    await an.analyze(fake_hass)

    types = {p.pattern_type for p in stored}
    assert "time_routine" in types, f"time detector didn't run in analyze(); got {types}"
    assert "sequence" in types, f"sequence detector didn't run in analyze(); got {types}"


async def test_analyze_survives_without_recorder(pa, tmp_path, monkeypatch, fake_hass):
    # fake_hass has no recorder → _fetch_numeric_sensor_history returns {}; the
    # sequence detector (which now takes sensor_hist) must still work with {}.
    db = str(tmp_path / "b.db")
    conn = sqlite3.connect(db); conn.executescript(_SCHEMA)
    base = datetime.now() - timedelta(days=14)
    for d in range(12):
        t = (base + timedelta(days=d)).replace(hour=20, minute=0, second=17)
        _ins(conn, "light.hall", "on", t)
        _ins(conn, "light.kitchen", "on", t + timedelta(seconds=45))
    conn.commit(); conn.close()

    an, stored = _capture(pa, db, monkeypatch)
    await an.analyze(fake_hass)          # must not raise despite empty sensor_hist
    assert any(p.pattern_type == "sequence" for p in stored)


async def test_analyze_wires_numeric_trigger_from_sensor_history(pa, tmp_path, monkeypatch, fake_hass):
    # Prove the numeric detector is wired into analyze() AND receives the fetched
    # sensor history: a heater that comes on while a temp sensor reads cold.
    db = str(tmp_path / "c.db")
    conn = sqlite3.connect(db); conn.executescript(_SCHEMA)
    base = datetime.now() - timedelta(days=16)
    action_eps = []
    for d in range(12):   # 12 occ -> confidence 0.8, clears the store bar
        t = (base + timedelta(days=d)).replace(hour=6, minute=2, second=17)
        _ins(conn, "switch.space_heater", "on", t)
        action_eps.append(t.timestamp())
    conn.commit(); conn.close()

    series = [(base.timestamp() - 3600 + i * 900, 72.0 + (i % 5)) for i in range(200)]
    series += [(ep - 1, 61.0) for ep in action_eps]           # cold right before each

    async def _fake_fetch(hass):
        return {"sensor.living_room_temperature": series}

    an, stored = _capture(pa, db, monkeypatch)
    monkeypatch.setattr(an, "_fetch_numeric_sensor_history", _fake_fetch)
    await an.analyze(fake_hass)

    nt = [p for p in stored if p.pattern_type == "numeric_trigger"]
    assert nt, "numeric_trigger detector not wired into analyze()"
    assert nt[0].details["op"] == "below"
    assert nt[0].details["action"]["entity"] == "switch.space_heater"
