"""pattern_analyzer: cross-reference learned suggestions against the household's
EXISTING automations and retire ones already covered.

The match is on OUTCOME/INTENT — the triggering situation plus the resulting
end-state (service mapped to its outcome) — not literal YAML, so "the same
outcome reached a different way" (a different service, structure, or params) is
recognised as already automated. Both the trigger and the outcome must overlap,
so a merely-shared trigger or outcome does not falsely suppress a genuinely
different automation.
"""
import json
import sqlite3

import pytest


@pytest.fixture
def pa(load):
    return load("pattern_analyzer")


# ── the outcome / intent matcher (pure) ──────────────────────────────────────

def test_outcome_of_action_maps_service_to_end_state(pa):
    f = pa._outcome_of_action
    assert f({"service": "cover.open_cover", "entity_id": "cover.bay_2"}) == ("cover.bay_2", "open")
    assert f({"action": "cover.close_cover", "target": {"entity_id": "cover.bay_2"}}) == ("cover.bay_2", "closed")
    assert f({"service": "light.turn_on", "entity_id": "light.porch"}) == ("light.porch", "on")
    assert f({"service": "lock.lock", "entity_id": "lock.front"}) == ("lock.front", "locked")
    assert f({"service": "scene.turn_on", "entity_id": "scene.movie"}) == ("scene.movie", "activate")
    # same OUTCOME via a different service: position 100 == open
    assert f({"service": "cover.set_cover_position", "entity_id": "cover.bay_2",
              "data": {"position": 100}}) == ("cover.bay_2", "open")
    assert f({"service": "cover.set_cover_position", "entity_id": "cover.bay_2",
              "position": 0}) == ("cover.bay_2", "closed")
    # non-actuating / unresolvable → None
    assert f({"service": "cover.open_cover"}) is None          # no entity
    assert f({"entity_id": "cover.bay_2"}) is None             # no service
    assert f({"delay": 5}) is None


def test_trigger_situation_normalizes(pa):
    g = pa._trigger_situation
    assert g({"platform": "state", "entity_id": "device_tracker.pixel_9_pro", "to": "home"}) == \
        ("device_tracker.pixel_9_pro", "to:home")
    # new-style key compares equal to old-style
    assert g({"trigger": "state", "entity_id": "device_tracker.pixel_9_pro", "to": "home"}) == \
        ("device_tracker.pixel_9_pro", "to:home")
    assert g({"platform": "numeric_state", "entity_id": "sensor.temp", "above": 75}) == \
        ("sensor.temp", "above:75")
    assert g({"platform": "time", "at": "18:00:00"}) == ("time", "at:18:00:00")


def test_intent_is_covered_requires_both_trigger_and_outcome(pa):
    # suggestion: when pixel arrives home → open bay 2
    sug = pa.automation_intent(
        {"platform": "state", "entity_id": "device_tracker.pixel_9_pro", "to": "home"},
        {"service": "cover.open_cover", "entity_id": "cover.bay_2"})
    same = pa.automation_intent(
        {"trigger": "state", "entity_id": "device_tracker.pixel_9_pro", "to": "home"},
        {"action": "cover.open_cover", "target": {"entity_id": "cover.bay_2"}})
    diff_outcome = pa.automation_intent(
        {"platform": "state", "entity_id": "device_tracker.pixel_9_pro", "to": "home"},
        {"service": "light.turn_on", "entity_id": "light.porch"})
    diff_trigger = pa.automation_intent(
        {"platform": "state", "entity_id": "device_tracker.other", "to": "home"},
        {"service": "cover.open_cover", "entity_id": "cover.bay_2"})
    assert pa.intent_is_covered(sug, [same]) is True            # same intent, different form
    assert pa.intent_is_covered(sug, [diff_outcome]) is False   # same trigger, other outcome
    assert pa.intent_is_covered(sug, [diff_trigger]) is False   # same outcome, other trigger
    assert pa.intent_is_covered(sug, []) is False


def test_same_outcome_via_position_matches_open_cover(pa):
    sug = pa.automation_intent(
        {"platform": "state", "entity_id": "device_tracker.pixel_9_pro", "to": "home"},
        {"service": "cover.open_cover", "entity_id": "cover.bay_2"})
    existing = pa.automation_intent(
        {"trigger": "state", "entity_id": "device_tracker.pixel_9_pro", "to": "home"},
        {"action": "cover.set_cover_position", "target": {"entity_id": "cover.bay_2"},
         "data": {"position": 100}})
    assert pa.intent_is_covered(sug, [existing]) is True


# ── reading existing automations from automations.yaml ───────────────────────

def test_read_existing_automation_intents(pa, tmp_path):
    import yaml
    path = tmp_path / "automations.yaml"
    path.write_text(yaml.dump([
        {   # new-style keys + target block
            "id": "1", "alias": "bay 2",
            "triggers": [{"trigger": "state", "entity_id": "device_tracker.pixel_9_pro", "to": "home"}],
            "actions": [{"action": "cover.open_cover", "target": {"entity_id": "cover.bay_2"}}],
        },
        {   # old-style keys
            "id": "2", "alias": "porch",
            "trigger": [{"platform": "time", "at": "18:00:00"}],
            "action": [{"service": "light.turn_on", "entity_id": "light.porch"}],
        },
    ]))
    intents = pa._read_existing_automation_intents(str(path))
    assert len(intents) == 2
    all_out = set().union(*[o for _, o in intents])
    assert ("cover.bay_2", "open") in all_out
    assert ("light.porch", "on") in all_out


def test_read_existing_missing_file_is_empty(pa, tmp_path):
    assert pa._read_existing_automation_intents(str(tmp_path / "nope.yaml")) == []


# ── end to end: covered pending suggestions are retired to 'superseded' ───────

def _make_db(tmp_path):
    db = str(tmp_path / "patterns.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, "
        "description TEXT, automation_yaml TEXT, confidence REAL, pattern_count INTEGER, "
        "pattern_type TEXT, entity_ids TEXT, details TEXT, status TEXT)")
    conn.commit()
    conn.close()
    return db


def _insert(db, auto_yaml, status="pending"):
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO suggestions (created, description, automation_yaml, confidence, "
        "pattern_count, pattern_type, entity_ids, details, status) "
        "VALUES ('t','d',?,0.9,5,'sequence','[]','{}',?)", (auto_yaml, status))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def _status(db, rid):
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status FROM suggestions WHERE id = ?", (rid,)).fetchone()
    conn.close()
    return row[0]


def test_suppress_retires_covered_suggestion_only(pa, tmp_path):
    db = _make_db(tmp_path)
    covered = _insert(db, json.dumps({
        "alias": "open bay 2 when pixel arrives",
        "trigger": {"platform": "state", "entity_id": "device_tracker.pixel_9_pro", "to": "home"},
        "action": {"service": "cover.open_cover", "entity_id": "cover.bay_2"}}))
    novel = _insert(db, json.dumps({
        "alias": "porch light at sunset",
        "trigger": {"platform": "sun", "event": "sunset"},
        "action": {"service": "light.turn_on", "entity_id": "light.porch"}}))

    an = pa.PatternAnalyzer()
    an._db = db
    # existing automation opens bay 2 when pixel arrives — a DIFFERENT form
    # (new-style keys + target block) than the learned suggestion above.
    existing = [pa.automation_intent(
        {"trigger": "state", "entity_id": "device_tracker.pixel_9_pro", "to": "home"},
        {"action": "cover.open_cover", "target": {"entity_id": "cover.bay_2"}})]

    moved = an._suppress_superseded_suggestions(existing)
    assert moved == 1
    assert _status(db, covered) == "superseded"   # already automated → retired
    assert _status(db, novel) == "pending"        # genuinely new → still shown


def test_suppress_noop_without_existing(pa, tmp_path):
    db = _make_db(tmp_path)
    rid = _insert(db, json.dumps({
        "alias": "x", "trigger": {"platform": "time", "at": "18:00:00"},
        "action": {"service": "light.turn_on", "entity_id": "light.porch"}}))
    an = pa.PatternAnalyzer()
    an._db = db
    assert an._suppress_superseded_suggestions([]) == 0
    assert _status(db, rid) == "pending"


# ── kill switch ──────────────────────────────────────────────────────────────

def test_cross_reference_kill_switch(pa, monkeypatch):
    import types
    cfg = types.SimpleNamespace(get=lambda key, default=None: default)
    monkeypatch.setitem(__import__("sys").modules, f"{pa.__package__}.jarvis_config", cfg)
    assert pa._cross_reference_enabled() is True            # default ON
    cfg.get = lambda key, default=None: False
    assert pa._cross_reference_enabled() is False           # explicit off
