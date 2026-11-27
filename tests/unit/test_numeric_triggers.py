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
    # synthetic temp history: a continuous warm run-up into each heater-on, then a
    # cold dip right before it — a genuine warm→cold CROSSING the detector requires.
    series = []
    warm_t = base - timedelta(hours=2)
    for i in range(40):                                     # baseline warm ~72 (high side)
        series.append((warm_t.timestamp() + i * 900, 72.0 + (i % 5)))
    for ep in action_epochs:
        series += [(ep - k, 72.0) for k in (1800, 1500, 1200, 900, 600, 300)]
        series.append((ep - 1, 61.0))                       # cold crossing just before
    hist = {"sensor.living_room_temperature": series}
    pats = pa.PatternAnalyzer()._find_numeric_triggers(conn, hist)
    m = [p for p in pats if p.details["action"]["entity"] == "switch.space_heater"]
    assert m, "expected a numeric trigger for heater-when-cold"
    assert m[0].details["op"] == "below"
    assert m[0].details["threshold"] <= 65.0
    assert "below" in m[0].description
    conn.close()


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
    conn.close()


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


# ── §7D: area scoping + genuine-crossing requirement ────────────────────────────

def test_same_area_numeric_ok(pa):
    ea = {"sensor.lr_temp": "living_room", "switch.lr_lamp": "living_room",
          "sensor.bsmt_hum": "basement", "cover.garage": "garage"}
    assert pa._same_area_numeric_ok("sensor.lr_temp", "switch.lr_lamp", ea) is True
    assert pa._same_area_numeric_ok("sensor.bsmt_hum", "switch.lr_lamp", ea) is False  # cross-room
    assert pa._same_area_numeric_ok("sensor.bsmt_hum", "cover.garage", ea) is True     # cover exempt
    assert pa._same_area_numeric_ok("sensor.bsmt_hum", "climate.thermo", ea) is True   # climate exempt
    assert pa._same_area_numeric_ok("sensor.unknown", "switch.lr_lamp", ea) is True    # unknown → allow
    assert pa._same_area_numeric_ok("sensor.lr_temp", "switch.lr_lamp", {}) is True     # no map → allow


def test_crossed_into(pa):
    # warm (72) then dips below 65 by t → a genuine crossing
    assert pa._crossed_into([0., 600., 1200., 1799.], [72., 72., 72., 61.],
                            "below", 65.0, 1800.0) is True
    # always below → no untriggered reading in the window → not a crossing
    assert pa._crossed_into([0., 600., 1200., 1799.], [61., 61., 61., 61.],
                            "below", 65.0, 1800.0) is False
    # crosses down then back up → ends untriggered → not a crossing INTO at t
    assert pa._crossed_into([0., 600., 1799.], [61., 61., 72.],
                            "below", 65.0, 1800.0) is False
    # above direction
    assert pa._crossed_into([0., 600., 1799.], [60., 60., 80.],
                            "above", 71.0, 1800.0) is True
    # too few readings in the window
    assert pa._crossed_into([0.0], [61.0], "below", 65.0, 1800.0) is False


def test_detector_rejects_cross_room_sensor(pa, tmp_path):
    conn = _conn(tmp_path / "xr.db")
    base = datetime.now() - timedelta(days=12)
    action_epochs = []
    for d in range(8):
        t = base + timedelta(days=d, hours=6, seconds=137)
        conn.execute("INSERT INTO state_changes (timestamp, entity_id, domain, "
                     "old_state, new_state, hour, day_of_week, person) "
                     "VALUES (?,?,?,?,?,?,?,?)",
                     (t.isoformat(), "switch.living_room_lamp", "switch", "off", "on",
                      t.hour, t.weekday(), "unknown"))
        action_epochs.append(t.timestamp())
    conn.commit()
    series = [(base.timestamp() - 7200 + i * 900, 72.0 + (i % 5)) for i in range(40)]
    for ep in action_epochs:
        series += [(ep - k, 72.0) for k in (1800, 1500, 1200, 900, 600, 300)]
        series.append((ep - 1, 61.0))
    hist = {"sensor.basement_temperature": series}
    # The sensor is in a DIFFERENT known area than the lamp → rejected.
    occ_ctx = {"entity_area": {"sensor.basement_temperature": "basement",
                               "switch.living_room_lamp": "living_room"}}
    pats = pa.PatternAnalyzer()._find_numeric_triggers(conn, hist, occ_ctx)
    assert not [p for p in pats if p.details["action"]["entity"] == "switch.living_room_lamp"]
    # Same area → the same correlation IS learned.
    occ_ctx["entity_area"]["sensor.basement_temperature"] = "living_room"
    pats2 = pa.PatternAnalyzer()._find_numeric_triggers(conn, hist, occ_ctx)
    assert [p for p in pats2 if p.details["action"]["entity"] == "switch.living_room_lamp"]
    conn.close()


def test_detector_rejects_passive_below_without_crossing(pa, tmp_path):
    conn = _conn(tmp_path / "pb.db")
    base = datetime.now() - timedelta(days=12)
    action_epochs = []
    for d in range(8):
        t = base + timedelta(days=d, hours=6, seconds=137)
        conn.execute("INSERT INTO state_changes (timestamp, entity_id, domain, "
                     "old_state, new_state, hour, day_of_week, person) "
                     "VALUES (?,?,?,?,?,?,?,?)",
                     (t.isoformat(), "switch.space_heater", "switch", "off", "on",
                      t.hour, t.weekday(), "unknown"))
        action_epochs.append(t.timestamp())
    conn.commit()
    # Warm history far in the past gives a clear high side, but near every action
    # the sensor has ALREADY been cold for 2h — so it never crosses in-window.
    series = [(base.timestamp() - 300000 + i * 900, 75.0 + (i % 4)) for i in range(300)]
    for ep in action_epochs:
        series += [(ep - k, 61.0) for k in range(0, 7200, 600)]
    hist = {"sensor.temp": series}
    pats = pa.PatternAnalyzer()._find_numeric_triggers(conn, hist, {})
    assert not [p for p in pats if p.details["action"]["entity"] == "switch.space_heater"]
    conn.close()


# ── §7H-2: excluded action entities never yield a numeric trigger ───────────────

def test_numeric_trigger_skips_excluded_action(pa, tmp_path):
    conn = _conn(tmp_path / "ex.db")
    base = datetime.now() - timedelta(days=12)
    action_epochs = []
    for d in range(8):
        t = base + timedelta(days=d, hours=6, seconds=137)
        conn.execute("INSERT INTO state_changes (timestamp, entity_id, domain, "
                     "old_state, new_state, hour, day_of_week, person) "
                     "VALUES (?,?,?,?,?,?,?,?)",
                     (t.isoformat(), "switch.space_heater", "switch", "off", "on",
                      t.hour, t.weekday(), "unknown"))
        action_epochs.append(t.timestamp())
    conn.commit()
    series = []
    warm_t = base - timedelta(hours=2)
    for i in range(40):
        series.append((warm_t.timestamp() + i * 900, 72.0 + (i % 5)))
    for ep in action_epochs:
        series += [(ep - k, 72.0) for k in (1800, 1500, 1200, 900, 600, 300)]
        series.append((ep - 1, 61.0))
    hist = {"sensor.living_room_temperature": series}
    # Baseline: without exclusion the heater-when-cold trigger IS learned …
    learned = pa.PatternAnalyzer()._find_numeric_triggers(conn, hist)
    assert [p for p in learned if p.details["action"]["entity"] == "switch.space_heater"]
    # … but an excluded heater yields nothing.
    pats = pa.PatternAnalyzer()._find_numeric_triggers(
        conn, hist, {}, {"switch.space_heater"})
    assert not [p for p in pats if p.details["action"]["entity"] == "switch.space_heater"]
    conn.close()
