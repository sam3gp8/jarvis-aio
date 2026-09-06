"""device / button triggers — "press → scene/action", learned from history.

Modern HA surfaces button & remote presses as event.* entities whose event_type
attribute holds the press. Those are logged (opt-in) with the event_type as the
value, so the sequence detector mines "button press → action" with no new
detector. On emission an event trigger becomes a state trigger on the entity
plus a template condition matching the specific press (the reliable general HA
form). Scenes are logged as actions so "button → scene" resolves to scene.turn_on.
"""
import json
import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def pa(load):
    return load("pattern_analyzer")


@pytest.fixture
def cc(load):
    return load("cognitive_core")


@pytest.fixture
def logger(cc, tmp_path):
    lg = cc.StateLogger.__new__(cc.StateLogger)
    lg._last_states = {}
    lg._db_path = str(tmp_path / "patterns.db")
    lg._init_db()
    return lg


# ── capture: event + scene gating in the logger ──────────────────────────────

def test_event_entity_needs_optin(logger):
    # without force_include, event.* is filtered like other noisy domains
    logger.log_state_change("event.remote", "", "single", force_include=False)
    with sqlite3.connect(logger._db_path) as c:
        assert c.execute("SELECT COUNT(*) FROM state_changes "
                         "WHERE domain='event'").fetchone()[0] == 0
    # opted in (force_include) → logged with the event_type as the value
    logger.log_state_change("event.remote", "", "single", force_include=True)
    with sqlite3.connect(logger._db_path) as c:
        row = c.execute("SELECT new_state FROM state_changes "
                        "WHERE domain='event'").fetchone()
    assert row and row[0] == "single"


def test_scene_activation_is_logged(logger):
    # scenes are no longer meta-skipped — they're valid action targets
    logger.log_state_change("scene.movie_night", "", "activated")
    with sqlite3.connect(logger._db_path) as c:
        row = c.execute("SELECT new_state FROM state_changes "
                        "WHERE domain='scene'").fetchone()
    assert row and row[0] == "activated"


def test_button_optin_flag(cc, monkeypatch):
    import sys, types
    pkg = cc.__name__.rsplit(".", 1)[0]              # the package load() used
    fake = types.SimpleNamespace(get=lambda k, d=None:
                                 True if k == "pattern_learn_buttons" else d)
    monkeypatch.setitem(sys.modules, f"{pkg}.jarvis_config", fake)
    assert cc._pattern_opted_in("event.remote", "") is True
    fake.get = lambda k, d=None: False
    assert cc._pattern_opted_in("event.remote", "") is False


# ── emission: event trigger → state trigger + template condition ─────────────

def test_generate_event_press_automation(pa):
    p = pa.DetectedPattern(
        pattern_type="sequence", description="x",
        entity_ids=["event.living_remote", "scene.movie_night"],
        confidence=0.8, occurrences=9,
        details={"trigger": {"entity": "event.living_remote", "state": "single"},
                 "action": {"entity": "scene.movie_night", "state": "activated"},
                 "delay_seconds": 0, "condition": None})
    auto = json.loads(pa.PatternAnalyzer()._generate_automation(p))
    # trigger fires on any event from the entity
    assert auto["trigger"] == {"platform": "state", "entity_id": "event.living_remote"}
    # the specific press is matched by a template condition
    tmpl = [c for c in auto["condition"] if c["condition"] == "template"]
    assert tmpl and "event_type == 'single'" in tmpl[0]["value_template"]
    # the action is the scene activation
    assert auto["action"][-1] == {"service": "scene.turn_on",
                                  "entity_id": "scene.movie_night"}
    assert "press" in auto["alias"]


def test_event_trigger_survives_normalize(pa):
    auto = pa.normalize_suggestion_automation(json.dumps({
        "alias": "x",
        "trigger": {"platform": "state", "entity_id": "event.living_remote"},
        "condition": [{"condition": "template",
                       "value_template":
                           "{{ trigger.to_state.attributes.event_type == 'single' }}"}],
        "action": [{"service": "scene.turn_on",
                    "target": {"entity_id": "scene.movie_night"}}],
    }))
    assert auto["installable"] is True
    assert auto["trigger"][0]["trigger"] == "state"
    assert auto["condition"][0]["condition"] == "template"


def test_service_for_scene(pa):
    assert pa.service_for("scene.movie_night", "activated") == {
        "service": "scene.turn_on", "entity_id": "scene.movie_night"}


def test_trigger_helpers_event_and_scene(pa):
    assert pa._trigger_for("event.remote", "double") == {
        "platform": "state", "entity_id": "event.remote"}
    assert pa._trigger_extra_conditions("event.remote", "double")[0]["condition"] == "template"
    assert pa._trigger_extra_conditions("light.hall", "on") == []
    assert pa._trigger_phrase("event.remote", "double") == "When event.remote is pressed (double)"
    assert pa._trigger_phrase("scene.movie", "activated") == "When scene.movie is activated"


# ── end-to-end: a button→scene sequence is detected and emitted ──────────────

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
                 (when.isoformat(), eid, dom, "", st, when.hour,
                  when.weekday(), "unknown"))


def test_button_to_scene_detected_and_emitted(pa, tmp_path):
    conn = _conn(tmp_path / "b.db")
    base = datetime.now() - timedelta(days=12)
    for d in range(8):
        t = base + timedelta(days=d, hours=20, seconds=137)
        _ins(conn, "event.living_remote", "single", t)
        _ins(conn, "scene.movie_night", "activated", t + timedelta(seconds=5))
    conn.commit()
    pats = pa.PatternAnalyzer()._find_sequence_patterns(conn, None, None)
    m = [p for p in pats if p.details["trigger"]["entity"] == "event.living_remote"
         and p.details["action"]["entity"] == "scene.movie_night"]
    assert m, "expected button-press → scene sequence"
    assert "is pressed (single)" in m[0].description
    auto = json.loads(pa.PatternAnalyzer()._generate_automation(m[0]))
    assert auto["trigger"] == {"platform": "state", "entity_id": "event.living_remote"}
    assert auto["action"][-1]["service"] == "scene.turn_on"
    assert any(c["condition"] == "template" for c in auto["condition"])
