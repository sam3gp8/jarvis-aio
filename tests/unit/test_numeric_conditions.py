"""numeric_state *conditions* on learned sequences — "…and only while it's below X".

Reuses the same scorer as the numeric trigger, so the anti-spurious guards carry
over. Conditions accumulate into a list that HA ANDs; a time/sun condition and a
numeric condition can both attach to one suggestion.
"""
import json
import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def pa(load):
    return load("pattern_analyzer")


# ── the numeric-condition helper ─────────────────────────────────────────────

def _series(base, warm=72.0, cold=None, cold_at=None, n=200):
    """A mostly-warm sensor series; optionally cold at the given epochs."""
    s = [(base.timestamp() - 3600 + i * 900, warm + (i % 5)) for i in range(n)]
    for ep in (cold_at or []):
        s.append((ep - 1, cold))
    return s


def test_numeric_condition_when_cold(pa):
    base = datetime.now() - timedelta(days=12)
    times = [(base + timedelta(days=d, hours=6, seconds=137)).timestamp()
             for d in range(8)]
    hist = {"sensor.living_room_temperature": _series(base, cold=61.0, cold_at=times)}
    cond = pa._numeric_condition(times, hist)
    assert cond is not None
    assert cond["condition"] == "numeric_state"
    assert cond["entity_id"] == "sensor.living_room_temperature"
    assert cond["below"] <= 65.0


def test_numeric_condition_none_when_unrelated(pa):
    base = datetime.now() - timedelta(days=12)
    times = [(base + timedelta(days=d, hours=6, seconds=137)).timestamp()
             for d in range(8)]
    # sensor wanders independently of the action times
    hist = {"sensor.temp": [(base.timestamp() + i * 900, 60.0 + (i % 20))
                            for i in range(300)]}
    assert pa._numeric_condition(times, hist) is None


def test_numeric_condition_none_without_history(pa):
    times = [float(i) for i in range(8)]
    assert pa._numeric_condition(times, {}) is None


# ── detector: numeric condition attaches to a sequence, ANDed with time/sun ──

_SCHEMA = (
    "CREATE TABLE state_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "timestamp TEXT, entity_id TEXT, domain TEXT, old_state TEXT, "
    "new_state TEXT, area_id TEXT, hour INTEGER, day_of_week INTEGER, person TEXT)")


def _conn(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def _ins(conn, eid, st, when):
    dom = eid.split(".")[0]
    conn.execute("INSERT INTO state_changes (timestamp, entity_id, domain, "
                 "old_state, new_state, hour, day_of_week, person) "
                 "VALUES (?,?,?,?,?,?,?,?)",
                 (when.isoformat(), eid, dom, "off", st, when.hour,
                  when.weekday(), "unknown"))


def test_sequence_gets_numeric_and_sun_conditions(pa, tmp_path):
    conn = _conn(tmp_path / "s.db")
    base = datetime.now() - timedelta(days=12)
    action_eps = []
    for d in range(8):
        t = base + timedelta(days=d, hours=20, seconds=137)   # 8pm → dark
        _ins(conn, "binary_sensor.hall_motion", "on", t)
        act = t + timedelta(seconds=30)
        _ins(conn, "switch.space_heater", "on", act)
        action_eps.append(act.timestamp())
    conn.commit()
    hist = {"sensor.hall_temperature": _series(base, cold=60.0, cold_at=action_eps)}
    pats = pa.PatternAnalyzer()._find_sequence_patterns(conn, 40.7, -74.0, hist)
    m = [p for p in pats
         if p.details["trigger"]["entity"] == "binary_sensor.hall_motion"]
    assert m, "expected the motion→heater sequence"
    conds = m[0].details["condition"]
    assert isinstance(conds, list)
    kinds = {c["condition"] for c in conds}
    # a time-based discriminator (sun where dark, else a time window) AND a
    # numeric condition both attach — the exact time kind depends on the
    # host timezone, so accept either.
    assert "numeric_state" in kinds and ("sun" in kinds or "time" in kinds)
    num = next(c for c in conds if c["condition"] == "numeric_state")
    assert num["entity_id"] == "sensor.hall_temperature" and num["below"] <= 62.0
    assert "below" in m[0].description


# ── emission: a list of conditions becomes HA's ANDed condition block ─────────

def test_generate_emits_multiple_conditions(pa):
    p = pa.DetectedPattern(
        pattern_type="sequence", description="x",
        entity_ids=["binary_sensor.hall_motion", "switch.space_heater"],
        confidence=0.8, occurrences=9,
        details={"trigger": {"entity": "binary_sensor.hall_motion", "state": "on"},
                 "action": {"entity": "switch.space_heater", "state": "on"},
                 "delay_seconds": 30,
                 "condition": [
                     {"condition": "sun", "after": "sunset", "before": "sunrise"},
                     {"condition": "numeric_state",
                      "entity_id": "sensor.hall_temperature", "below": 62.0}]})
    auto = json.loads(pa.PatternAnalyzer()._generate_automation(p))
    assert isinstance(auto["condition"], list) and len(auto["condition"]) == 2
    assert {c["condition"] for c in auto["condition"]} == {"sun", "numeric_state"}


def test_normalize_preserves_condition_list(pa):
    norm = pa.normalize_suggestion_automation(json.dumps({
        "alias": "x",
        "trigger": {"platform": "state", "entity_id": "binary_sensor.hall_motion",
                    "to": "on"},
        "condition": [
            {"condition": "sun", "after": "sunset", "before": "sunrise"},
            {"condition": "numeric_state", "entity_id": "sensor.t", "below": 62.0}],
        "action": [{"service": "switch.turn_on",
                    "target": {"entity_id": "switch.space_heater"}}],
    }))
    assert len(norm["condition"]) == 2
    assert {c["condition"] for c in norm["condition"]} == {"sun", "numeric_state"}
