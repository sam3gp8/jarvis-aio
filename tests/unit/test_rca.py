"""Tests for root cause analysis (v6.37.0) — evidence gathering, cause ranking,
timeline assembly, and graceful degradation, against real-schema temp DBs."""
import sqlite3

import pytest

T0 = "2026-07-12 03:00:00"          # the focal moment used across scenarios


@pytest.fixture
def rca(load):
    return load("rca")


@pytest.fixture
def dbs(tmp_path):
    """patterns.db + conversations.db with the production schemas."""
    p = str(tmp_path / "patterns.db")
    a = str(tmp_path / "conversations.db")
    with sqlite3.connect(p) as c:
        c.executescript("""
            CREATE TABLE state_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, entity_id TEXT NOT NULL,
                domain TEXT NOT NULL, old_state TEXT, new_state TEXT NOT NULL,
                area_id TEXT, hour INTEGER, day_of_week INTEGER,
                triggered_by TEXT DEFAULT 'system');
            CREATE TABLE commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, text TEXT NOT NULL,
                handled_by TEXT DEFAULT 'agent', entity_ids TEXT DEFAULT '[]',
                person TEXT DEFAULT 'unknown', hour INTEGER, day_of_week INTEGER);
        """)
    with sqlite3.connect(a) as c:
        c.executescript("""
            CREATE TABLE activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, entity_id TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'other',
                urgency TEXT NOT NULL DEFAULT 'low',
                message TEXT NOT NULL DEFAULT '', was_spoken INTEGER DEFAULT 0);
        """)
    return p, a


def sc(db, ts, eid, old, new, *, area=None, hour=None, trig="system"):
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO state_changes (timestamp, entity_id, domain, "
                  "old_state, new_state, area_id, hour, day_of_week, triggered_by) "
                  "VALUES (?,?,?,?,?,?,?,?,?)",
                  (ts, eid, eid.split(".")[0], old, new, area, hour, 0, trig))


def cmd(db, ts, text, person="sam"):
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO commands (timestamp, text, person) VALUES (?,?,?)",
                  (ts, text, person))


def act(db, ts, eid, message, category="action"):
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO activity_log (timestamp, entity_id, category, message) "
                  "VALUES (?,?,?,?)", (ts, eid, category, message))


def _analyze(rca, dbs, eid, **kw):
    p, a = dbs
    return rca.analyze(eid, patterns_db=p, activity_db=a, **kw)


# ── heuristics ───────────────────────────────────────────────────────────────

def test_attributed_trigger_wins(rca, dbs):
    p, _ = dbs
    sc(p, T0, "light.kitchen", "on", "off", hour=3, trig="automation.night_mode")
    res = _analyze(rca, dbs, "light.kitchen")
    top = res["candidates"][0]
    assert top["kind"] == "attributed" and top["confidence"] == 0.95
    assert "automation.night_mode" in res["summary"]


def test_unavailable_cascade_by_shared_name(rca, dbs):
    p, _ = dbs
    # T-form timestamp on the upstream row proves T/space normalization works.
    sc(p, "2026-07-12T02:59:30", "sensor.zigbee_hub_status", "ok", "unavailable")
    sc(p, T0, "binary_sensor.zigbee_hub_motion", "on", "unavailable", hour=3)
    res = _analyze(rca, dbs, "binary_sensor.zigbee_hub_motion")
    top = res["candidates"][0]
    assert top["kind"] == "cascade" and top["confidence"] == 0.9
    assert "sensor.zigbee_hub_status" in top["cause"]


def test_command_cause_names_person(rca, dbs):
    p, _ = dbs
    cmd(p, "2026-07-12 02:59:00", "turn off the kitchen lights", person="sam")
    sc(p, T0, "light.kitchen", "on", "off", hour=3)
    res = _analyze(rca, dbs, "light.kitchen")
    top = res["candidates"][0]
    assert top["kind"] == "command" and "sam" in top["cause"]


def test_command_outside_window_ignored(rca, dbs):
    p, _ = dbs
    cmd(p, "2026-07-12 02:40:00", "turn off the kitchen lights")  # 20 min before
    sc(p, T0, "light.kitchen", "on", "off", hour=3)
    res = _analyze(rca, dbs, "light.kitchen")
    assert all(c["kind"] != "command" for c in res["candidates"])


def test_jarvis_action_cause(rca, dbs):
    p, a = dbs
    act(a, "2026-07-12 02:59:15", "lock.front", "Lockdown: locked the front door",
        category="lockdown")
    sc(p, T0, "lock.front", "unlocked", "locked", hour=3)
    res = _analyze(rca, dbs, "lock.front")
    top = res["candidates"][0]
    assert top["kind"] == "jarvis" and "Lockdown" in top["cause"]


def test_schedule_recurrence_hint(rca, dbs):
    p, _ = dbs
    for day in range(5, 10):                       # five prior days, same hour
        sc(p, f"2026-07-{day:02d} 03:00:00", "climate.heat", "idle", "heating", hour=3)
    sc(p, T0, "climate.heat", "idle", "heating", hour=3)
    res = _analyze(rca, dbs, "climate.heat")
    top = res["candidates"][0]
    assert top["kind"] == "schedule"
    assert "automation or schedule" in top["evidence"]


def test_area_clue_present_but_command_outranks(rca, dbs):
    p, _ = dbs
    sc(p, "2026-07-12 02:59:40", "binary_sensor.den_motion", "off", "on", area="den")
    cmd(p, "2026-07-12 02:59:00", "turn on the den lamp")
    sc(p, T0, "light.den_lamp", "off", "on", area="den", hour=3)
    res = _analyze(rca, dbs, "light.den_lamp")
    kinds = [c["kind"] for c in res["candidates"]]
    assert "command" in kinds and "area" in kinds
    assert kinds.index("command") < kinds.index("area")


# ── degradation + plumbing ───────────────────────────────────────────────────

def test_unknown_when_no_history(rca, dbs):
    res = _analyze(rca, dbs, "light.nowhere")
    assert res["candidates"][0]["kind"] == "unknown"
    assert "couldn't determine" in res["summary"]


def test_missing_databases_are_graceful(rca):
    res = rca.analyze("light.x", patterns_db="/nope/p.db", activity_db="/nope/a.db")
    assert res["candidates"][0]["kind"] == "unknown"


def test_timeline_merges_and_sorts_all_sources(rca, dbs):
    p, a = dbs
    cmd(p, "2026-07-12 02:58:00", "goodnight")
    act(a, "2026-07-12 02:59:00", "", "Engaging night mode")
    sc(p, "2026-07-12 02:59:30", "light.hall", "on", "off")
    sc(p, T0, "light.kitchen", "on", "off", hour=3)
    res = _analyze(rca, dbs, "light.kitchen")
    tl = res["timeline"]
    assert [i["src"] for i in tl] == ["command", "jarvis", "change", "change"]
    assert [i["t"] for i in tl] == sorted(i["t"] for i in tl)


def test_event_time_selects_the_older_change(rca, dbs):
    p, _ = dbs
    sc(p, "2026-07-12 01:00:00", "light.kitchen", "off", "on", hour=1,
       trig="automation.early")
    sc(p, T0, "light.kitchen", "on", "off", hour=3)
    res = _analyze(rca, dbs, "light.kitchen", event_time="2026-07-12 01:30:00")
    assert res["event"]["new_state"] == "on"
    assert "automation.early" in res["summary"]


def test_tokens_filter_generic_words(rca):
    assert rca._tokens("binary_sensor.garage_hub_motion") == {"garage", "hub", "motion"}
    assert "sensor" not in rca._tokens("sensor.kitchen_sensor")
