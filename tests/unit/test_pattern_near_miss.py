"""Near-miss capture: patterns detected but below the store bar are surfaced so
"not enough data yet" is distinguishable from "nothing detected" (issue: a real
occupancy→door pattern that just hasn't recurred ~7 times yet).
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


def _db_with_sequence(tmp_path, name, occurrences):
    db = str(tmp_path / name)
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    base = datetime.now() - timedelta(days=occurrences + 2)
    for d in range(occurrences):
        t = base + timedelta(days=d, hours=18)
        for ent, st, off in (("binary_sensor.bay_occupancy", "on", 0),
                             ("cover.garage", "closed", 60)):
            tt = t + timedelta(seconds=off)
            conn.execute(
                "INSERT INTO state_changes (timestamp, entity_id, domain, "
                "old_state, new_state, area_id, hour, day_of_week, person) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (tt.isoformat(), ent, ent.split(".")[0], "off", st, "",
                 tt.hour, tt.weekday(), "unknown"))
    conn.commit()
    conn.close()
    return db


def _analyzer(pa, db, monkeypatch, store_result):
    an = pa.PatternAnalyzer()
    an._db = db
    monkeypatch.setattr(an, "_store_suggestion", lambda p: store_result)
    monkeypatch.setattr(an, "_store_person_pattern", lambda p: False)
    monkeypatch.setattr(an, "_promote_to_knowledge", lambda pats: 0)
    monkeypatch.setattr(pa, "_learned_threshold_delta", lambda: 0.0)
    return an


async def test_below_bar_sequence_is_a_near_miss(pa, tmp_path, monkeypatch, fake_hass):
    # 6 occurrences: confidence 6/(5*3)=0.4 < 0.65 store bar -> surfaced as building
    db = _db_with_sequence(tmp_path, "nm.db", 6)
    an = _analyzer(pa, db, monkeypatch, store_result=False)
    await an.analyze(fake_hass)
    nm = an._last_result.get("near_misses", [])
    m = [x for x in nm if "bay_occupancy" in (x.get("description") or "")]
    assert m, f"expected a near-miss for the 6x sequence; got {nm}"
    assert m[0]["occurrences"] == 6
    assert m[0]["needed"] == 10        # ceil(0.65 * 5 * 3)


async def test_above_bar_sequence_stores_not_near_miss(pa, tmp_path, monkeypatch, fake_hass):
    # 12 occurrences: confidence 12/15=0.8 >= 0.65 -> stored, NOT a near-miss
    db = _db_with_sequence(tmp_path, "st.db", 12)
    an = _analyzer(pa, db, monkeypatch, store_result=True)
    await an.analyze(fake_hass)
    assert an._last_result["new_suggestions"] >= 1
    nm = an._last_result.get("near_misses", [])
    assert not [x for x in nm if "bay_occupancy" in (x.get("description") or "")]
