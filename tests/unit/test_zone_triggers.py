"""zone triggers — learned departure/arrival automations.

When a person or device_tracker crossing home↔away consistently precedes an
action, the suggestion is emitted as HA's semantic zone trigger (enter/leave
zone.home) rather than a raw state trigger. Non-presence triggers and named-zone
states are left as state triggers.
"""
import json
import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def pa(load):
    return load("pattern_analyzer")


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_trigger_for_presence_becomes_zone(pa):
    assert pa._trigger_for("person.sam", "not_home") == {
        "platform": "zone", "entity_id": "person.sam",
        "zone": "zone.home", "event": "leave"}
    assert pa._trigger_for("person.sam", "home") == {
        "platform": "zone", "entity_id": "person.sam",
        "zone": "zone.home", "event": "enter"}
    assert pa._trigger_for("device_tracker.sam_phone", "not_home")["event"] == "leave"


def test_trigger_for_nonpresence_stays_state(pa):
    assert pa._trigger_for("binary_sensor.hall_motion", "on") == {
        "platform": "state", "entity_id": "binary_sensor.hall_motion", "to": "on"}
    # a named-zone state (not home/not_home) is left as a state trigger
    assert pa._trigger_for("person.sam", "work")["platform"] == "state"


def test_trigger_phrase(pa):
    assert pa._trigger_phrase("person.sam", "not_home") == "When person.sam leaves home"
    assert pa._trigger_phrase("person.sam", "home") == "When person.sam arrives home"
    assert pa._trigger_phrase("light.hall", "on") == "When light.hall turns on"


# ── emission ─────────────────────────────────────────────────────────────────

def test_generate_zone_leave_automation(pa):
    p = pa.DetectedPattern(
        pattern_type="sequence", description="x",
        entity_ids=["person.sam", "cover.garage"],
        confidence=0.8, occurrences=9,
        details={"trigger": {"entity": "person.sam", "state": "not_home"},
                 "action": {"entity": "cover.garage", "state": "closed"},
                 "delay_seconds": 30, "condition": None})
    auto = json.loads(pa.PatternAnalyzer()._generate_automation(p))
    assert auto["trigger"] == {"platform": "zone", "entity_id": "person.sam",
                               "zone": "zone.home", "event": "leave"}
    assert "leaves home" in auto["alias"]


def test_zone_trigger_survives_normalize(pa):
    auto = pa.normalize_suggestion_automation(json.dumps({
        "alias": "x",
        "trigger": {"platform": "zone", "entity_id": "person.sam",
                    "zone": "zone.home", "event": "leave"},
        "action": [{"service": "cover.close_cover",
                    "target": {"entity_id": "cover.garage"}}],
    }))
    assert auto["installable"] is True
    # HA modernized platform→trigger; the zone fields survive
    t = auto["trigger"][0]
    assert t["trigger"] == "zone" and t["event"] == "leave"
    assert t["zone"] == "zone.home" and t["entity_id"] == "person.sam"


# ── detector: a departure sequence is detected and converts on emission ──────

_SCHEMA = (
    "CREATE TABLE state_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "timestamp TEXT, entity_id TEXT, domain TEXT, old_state TEXT, "
    "new_state TEXT, area_id TEXT, hour INTEGER, day_of_week INTEGER, person TEXT)")


def _conn(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def _ins(conn, eid, st, when, old="home"):
    dom = eid.split(".")[0]
    conn.execute("INSERT INTO state_changes (timestamp, entity_id, domain, "
                 "old_state, new_state, hour, day_of_week, person) "
                 "VALUES (?,?,?,?,?,?,?,?)",
                 (when.isoformat(), eid, dom, old, st, when.hour,
                  when.weekday(), "unknown"))


def test_departure_sequence_detected_and_emitted_as_zone(pa, tmp_path):
    conn = _conn(tmp_path / "z.db")
    base = datetime.now() - timedelta(days=12)
    for d in range(8):
        t = base + timedelta(days=d, hours=8, seconds=137)
        _ins(conn, "person.sam", "not_home", t, old="home")
        _ins(conn, "cover.garage", "closed", t + timedelta(seconds=30), old="open")
    conn.commit()
    pats = pa.PatternAnalyzer()._find_sequence_patterns(conn, None, None)
    m = [p for p in pats if p.details["trigger"]["entity"] == "person.sam"
         and p.details["action"]["entity"] == "cover.garage"]
    assert m, "expected person-leaves → garage-closes sequence"
    assert m[0].details["trigger"]["state"] == "not_home"
    assert "leaves home" in m[0].description
    auto = json.loads(pa.PatternAnalyzer()._generate_automation(m[0]))
    assert auto["trigger"]["platform"] == "zone" and auto["trigger"]["event"] == "leave"
