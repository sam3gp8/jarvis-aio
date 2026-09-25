"""Tests for occupancy-aware, IFTTT-style automation suggestions (v7.100.0):
Phase 1 room-occupancy conditions, Phase 2 'hold until unoccupied', and Phase 3
confirmation choreographies. Detectors are driven off a synthetic state_changes
DB + occ_ctx so they stay unit-testable without a live Home Assistant."""
import json
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
"""


@pytest.fixture
def pa(load):
    return load("pattern_analyzer")


def _conn(path):
    c = sqlite3.connect(str(path))
    c.executescript(_SCHEMA)
    c.commit()
    c.row_factory = sqlite3.Row
    return c


def _add(conn, entity_id, new_state, when: datetime):
    dom = entity_id.split(".", 1)[0]
    conn.execute(
        "INSERT INTO state_changes (timestamp, entity_id, domain, old_state, "
        "new_state, area_id, hour, day_of_week) VALUES (?,?,?,?,?,?,?,?)",
        (when.isoformat(), entity_id, dom, "", new_state, "", when.hour, when.weekday()))


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_secs_to_hms(pa):
    assert pa._secs_to_hms(0) == "00:00:00"
    assert pa._secs_to_hms(90) == "00:01:30"
    assert pa._secs_to_hms(3661) == "01:01:01"


def test_occupancy_condition_when_room_occupied(pa):
    n = max(6, pa.MIN_OCCURRENCES + 2)
    # sensor on from 100..10000, off before/after → genuinely varies
    hist = {"binary_sensor.garage_occ": [(0.0, False), (100.0, True), (10000.0, False)]}
    occ = {"hist": hist,
           "entity_area": {"light.garage": "garage", "binary_sensor.garage_occ": "garage"},
           "area_sensors": {"garage": ["binary_sensor.garage_occ"]}}
    times = [200.0 + i for i in range(n)]           # all while occupied
    cond = pa._occupancy_condition("light.garage", times, occ)
    assert cond == {"condition": "state", "entity_id": "binary_sensor.garage_occ", "state": "on"}


def test_occupancy_condition_rejects_always_on_sensor(pa):
    n = max(6, pa.MIN_OCCURRENCES + 2)
    hist = {"binary_sensor.always": [(0.0, True), (100.0, True)]}   # never varies
    occ = {"hist": hist, "entity_area": {"light.x": "a", "binary_sensor.always": "a"},
           "area_sensors": {"a": ["binary_sensor.always"]}}
    assert pa._occupancy_condition("light.x", [200.0 + i for i in range(n)], occ) is None


def test_occupancy_condition_none_without_area(pa):
    assert pa._occupancy_condition("light.orphan", [1.0, 2.0, 3.0, 4.0, 5.0], {}) is None


def test_occupancy_hold_detects_until_unoccupied(pa):
    n = max(6, pa.MIN_OCCURRENCES + 2)
    # sensor toggles on/off; each 'clear' (on->off) is followed ~30s later by the light off
    series = []
    off_times = []
    base = 1000.0
    for i in range(n):
        on = base + i * 5000
        clear = on + 600
        series += [(on, True), (clear, False)]
        off_times.append(clear + 30)     # light off shortly after the room clears
    occ = {"hist": {"binary_sensor.occ": series},
           "entity_area": {"light.garage": "garage", "binary_sensor.occ": "garage"},
           "area_sensors": {"garage": ["binary_sensor.occ"]}}
    hold = pa._occupancy_hold("light.garage", off_times, occ)
    assert hold and hold["entity_id"] == "binary_sensor.occ"
    assert hold["for_seconds"] >= 0


def test_occupancy_hold_none_when_uncorrelated(pa):
    n = max(6, pa.MIN_OCCURRENCES + 2)
    series = [(0.0, False), (100.0, True), (200.0, False)]   # one clear only
    occ = {"hist": {"binary_sensor.occ": series},
           "entity_area": {"light.garage": "garage", "binary_sensor.occ": "garage"},
           "area_sensors": {"garage": ["binary_sensor.occ"]}}
    # off times nowhere near the single clear
    assert pa._occupancy_hold("light.garage", [50000.0 + i * 100 for i in range(n)], occ) is None


# ── Phase 1: occupancy condition in generated automations ────────────────────

def test_numeric_trigger_gets_occupancy_condition_installed(pa):
    an = pa.PatternAnalyzer()
    p = pa.DetectedPattern(
        pattern_type="numeric_trigger", description="lumens", entity_ids=["sensor.lux", "light.den"],
        confidence=1.0, occurrences=10,
        details={"trigger_sensor": "sensor.lux", "op": "below", "threshold": 40.0,
                 "action": {"entity": "light.den", "state": "on"},
                 "condition": [{"condition": "state", "entity_id": "binary_sensor.den_occ",
                                "state": "on"}]})
    norm = pa.normalize_suggestion_automation(an._generate_automation(p))
    assert norm["installable"] is True
    assert any(c.get("entity_id") == "binary_sensor.den_occ" for c in norm["condition"])


# ── Phase 2: hold-until-unoccupied choreography ──────────────────────────────

def _seq_until(pa, until):
    return pa.DetectedPattern(
        pattern_type="sequence", description="door->light hold", entity_ids=["binary_sensor.kd", "light.garage"],
        confidence=1.0, occurrences=10,
        details={"trigger": {"entity": "binary_sensor.kd", "state": "on"},
                 "action": {"entity": "light.garage", "state": "on"},
                 "delay_seconds": 5, "condition": None, "until_unoccupied": until})


def test_hold_until_unoccupied_builds_choreography(pa):
    an = pa.PatternAnalyzer()
    yml = an._generate_automation(_seq_until(
        pa, {"entity_id": "binary_sensor.garage_occ", "for_seconds": 60}))
    data = json.loads(yml)
    assert data["mode"] == "restart"
    kinds = [list(a.keys())[0] if isinstance(a, dict) else a for a in data["action"]]
    assert "wait_for_trigger" in kinds
    # ends by turning the light back off
    assert any(a.get("service") == "light.turn_off" for a in data["action"] if isinstance(a, dict))
    norm = pa.normalize_suggestion_automation(yml)
    assert norm["installable"] is True and norm["mode"] == "restart"


def test_sequence_without_until_stays_single(pa):
    an = pa.PatternAnalyzer()
    yml = an._generate_automation(_seq_until(pa, None))
    data = json.loads(yml)
    assert "mode" not in data
    assert not any(isinstance(a, dict) and "wait_for_trigger" in a for a in data["action"])


# ── Phase 3: confirmation choreography ───────────────────────────────────────

def test_confirm_sequence_generation_is_safe_and_installable(pa):
    an = pa.PatternAnalyzer()
    p = pa.DetectedPattern(
        pattern_type="confirm_sequence", description="garage confirm",
        entity_ids=["device_tracker.jeep", "cover.garage", "binary_sensor.bay_car"],
        confidence=1.0, occurrences=8,
        details={"trigger": {"entity": "device_tracker.jeep", "state": "home"},
                 "open": {"entity": "cover.garage", "state": "open"},
                 "close": {"entity": "cover.garage", "state": "closed"},
                 "confirm": {"entity": "binary_sensor.bay_car", "state": "on"},
                 "confirm_timeout": 120})
    yml = an._generate_automation(p)
    data = json.loads(yml)
    kinds = [list(a.keys())[0] if isinstance(a, dict) else a for a in data["action"]]
    assert kinds[0] == "service" and "wait_for_trigger" in kinds
    wait = next(a for a in data["action"] if isinstance(a, dict) and "wait_for_trigger" in a)
    assert wait["continue_on_timeout"] is False        # don't close on an unconfirmed step
    assert data["mode"] == "restart"
    assert "SAFETY" in data.get("description", "").upper()
    norm = pa.normalize_suggestion_automation(yml)
    assert norm["installable"] is True


def test_find_confirmation_sequences_detects_choreography(pa, tmp_path):
    conn = _conn(tmp_path / "sc.db")
    n = max(6, pa.MIN_OCCURRENCES + 3)
    base = datetime.now() - timedelta(days=28)
    for i in range(n):
        t = base + timedelta(days=i, hours=17)
        _add(conn, "device_tracker.jeep", "home", t)
        _add(conn, "cover.garage", "open", t + timedelta(seconds=60))
        _add(conn, "binary_sensor.bay_car", "on", t + timedelta(seconds=90))
        _add(conn, "cover.garage", "closed", t + timedelta(seconds=150))
    conn.commit()
    an = pa.PatternAnalyzer()
    occ = {"hist": {}, "entity_area": {"cover.garage": "garage"},
           "area_sensors": {"garage": ["binary_sensor.bay_car"]}}
    pats = an._find_confirmation_sequences(conn, occ)
    conn.close()
    assert pats, "expected a confirm_sequence pattern"
    d = pats[0].details
    assert pats[0].pattern_type == "confirm_sequence"
    assert d["trigger"]["entity"] == "device_tracker.jeep"
    assert d["open"]["entity"] == "cover.garage"
    assert d["confirm"]["entity"] == "binary_sensor.bay_car"


def test_confirm_sequence_gated_by_opt_in(pa, tmp_path):
    # The safety-sensitive Phase-3 finder must only run when the user opts in.
    conn_path = tmp_path / "gate.db"
    conn = _conn(conn_path)
    n = max(6, pa.MIN_OCCURRENCES + 3)
    base = datetime.now() - timedelta(days=28)
    for i in range(n):
        t = base + timedelta(days=i, hours=17)
        _add(conn, "device_tracker.jeep", "home", t)
        _add(conn, "cover.garage", "open", t + timedelta(seconds=60))
        _add(conn, "binary_sensor.bay_car", "on", t + timedelta(seconds=90))
        _add(conn, "cover.garage", "closed", t + timedelta(seconds=150))
    conn.commit()
    conn.close()
    an = pa.PatternAnalyzer()
    an._db = str(conn_path)
    occ = {"hist": {}, "entity_area": {"cover.garage": "garage"},
           "area_sensors": {"garage": ["binary_sensor.bay_car"]}}
    off = an._run_all_finders({}, None, None, {}, occ, confirm_enabled=False)
    assert not any(p.pattern_type == "confirm_sequence" for p in off)
    on = an._run_all_finders({}, None, None, {}, occ, confirm_enabled=True)
    assert any(p.pattern_type == "confirm_sequence" for p in on)


def test_explain_confirm_sequence(pa):
    out = pa.explain_suggestion("confirm_sequence", {
        "trigger": {"entity": "device_tracker.jeep"},
        "open": {"entity": "cover.garage"},
        "confirm": {"entity": "binary_sensor.bay_car"}}, 8)
    assert out["headline"] and out["evidence"]
    joined = " ".join(out["evidence"]).lower()
    assert "confirms" in joined and "review carefully" in joined
