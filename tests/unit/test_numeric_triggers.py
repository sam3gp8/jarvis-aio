"""numeric_state triggers — learn "when a sensor crosses a threshold, act".

The scorer must find a real below/above threshold when the action consistently
fires on one side AND the sensor genuinely crosses it, and reject spurious /
mixed / flat-sensor cases. The detector is fed synthetic sensor history so it's
testable without the recorder.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def pa(load):
    return load("pattern_analyzer")


# ── pure cores ───────────────────────────────────────────────────────────────

def test_value_at_bisects(pa):
    ep = [100.0, 200.0, 300.0]
    va = [10.0, 20.0, 30.0]
    assert pa._numeric_value_at(ep, va, 250.0) == 20.0
    assert pa._numeric_value_at(ep, va, 100.0) == 10.0
    assert pa._numeric_value_at(ep, va, 50.0) is None      # before first reading
    assert pa._numeric_value_at(ep, va, 999.0) == 30.0
    assert pa._numeric_value_at([], [], 1.0) is None


def test_nice_threshold_includes_observed_side(pa):
    assert pa._nice_threshold(64.0, "below") == 65.0       # below 65 includes 64
    assert pa._nice_threshold(63.2, "below") == 64.0
    assert pa._nice_threshold(72.0, "above") == 71.0       # above 71 includes 72
    assert pa._nice_threshold(72.8, "above") == 72.0


def test_trigger_below_when_action_fires_cold(pa):
    baseline = list(range(55, 81)) * 2                      # spans 55..80
    occ = [60, 61, 62, 63, 64]                              # action fires cold
    assert pa._numeric_trigger_from(occ, baseline) == {"below": 65.0}


def test_trigger_above_when_action_fires_hot(pa):
    baseline = list(range(55, 81)) * 2
    occ = [72, 73, 74, 75, 76]
    assert pa._numeric_trigger_from(occ, baseline) == {"above": 71.0}


def test_no_trigger_when_values_scattered(pa):
    baseline = list(range(55, 81)) * 2
    occ = [58, 64, 68, 72, 78]                              # no clear side
    assert pa._numeric_trigger_from(occ, baseline) is None


def test_no_trigger_on_flat_sensor(pa):
    assert pa._numeric_trigger_from([70, 70, 70, 70, 70], [70] * 12) is None


def test_no_trigger_without_real_crossing(pa):
    # Sensor is always low; occurrences are low too, but there's no high side to
    # cross — must NOT invent a "below" threshold.
    baseline = [58, 59, 60, 61, 62] * 3
    occ = [58, 59, 60, 58, 59]
    assert pa._numeric_trigger_from(occ, baseline) is None


# ── detector (synthetic sensor history) ──────────────────────────────────────

_SCHEMA = (
    "CREATE TABLE state_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "timestamp TEXT, entity_id TEXT, domain TEXT, old_state TEXT, "
    "new_state TEXT, area_id TEXT, hour INTEGER, day_of_week INTEGER, person TEXT)")


def _conn(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def test_detector_learns_cold_then_heater(pa, tmp_path):
    conn = _conn(tmp_path / "n.db")
    base = datetime.now() - timedelta(days=12)
    action_epochs = []
    for d in range(8):                                      # heater on 8 times
        t = base + timedelta(days=d, hours=6, seconds=137)
        conn.execute("INSERT INTO state_changes (timestamp, entity_id, domain, "
                     "old_state, new_state, hour, day_of_week, person) "
                     "VALUES (?,?,?,?,?,?,?,?)",
                     (t.isoformat(), "switch.space_heater", "switch", "off", "on",
                      t.hour, t.weekday(), "unknown"))
        action_epochs.append(t.timestamp())
    conn.commit()
    # synthetic temp history: mostly warm, but low right at each heater-on
    series = []
    warm_t = base - timedelta(hours=1)
    for i in range(200):                                    # baseline warm ~72
        series.append((warm_t.timestamp() + i * 900, 72.0 + (i % 5)))
    for ep in action_epochs:                                # dip cold just before
        series.append((ep - 1, 61.0))
    hist = {"sensor.living_room_temperature": series}
    pats = pa.PatternAnalyzer()._find_numeric_triggers(conn, hist)
    m = [p for p in pats if p.details["action"]["entity"] == "switch.space_heater"]
    assert m, "expected a numeric trigger for heater-when-cold"
    assert m[0].details["op"] == "below"
    assert m[0].details["threshold"] <= 65.0
    assert "below" in m[0].description


def test_detector_no_trigger_when_uncorrelated(pa, tmp_path):
    conn = _conn(tmp_path / "u.db")
    base = datetime.now() - timedelta(days=12)
    for d in range(8):
        t = base + timedelta(days=d, hours=6, seconds=137)
        conn.execute("INSERT INTO state_changes (timestamp, entity_id, domain, "
                     "old_state, new_state, hour, day_of_week, person) "
                     "VALUES (?,?,?,?,?,?,?,?)",
                     (t.isoformat(), "light.kitchen", "light", "off", "on",
                      t.hour, t.weekday(), "unknown"))
    conn.commit()
    # temp wanders with no relation to the light
    series = [(base.timestamp() + i * 900, 60.0 + (i % 20)) for i in range(300)]
    pats = pa.PatternAnalyzer()._find_numeric_triggers(
        conn, {"sensor.temp": series})
    assert not [p for p in pats if p.details["action"]["entity"] == "light.kitchen"]


# ── emission ─────────────────────────────────────────────────────────────────

def test_generate_numeric_state_automation(pa):
    import json
    p = pa.DetectedPattern(
        pattern_type="numeric_trigger", description="x",
        entity_ids=["sensor.living_room_temperature", "switch.space_heater"],
        confidence=0.8, occurrences=9,
        details={"trigger_sensor": "sensor.living_room_temperature",
                 "op": "below", "threshold": 65.0,
                 "action": {"entity": "switch.space_heater", "state": "on"}})
    auto = json.loads(pa.PatternAnalyzer()._generate_automation(p))
    assert auto["trigger"]["platform"] == "numeric_state"
    assert auto["trigger"]["entity_id"] == "sensor.living_room_temperature"
    assert auto["trigger"]["below"] == 65.0
