"""A learned automation is suggested once, and named for humans.

Two bugs this covers:
  • De-dup used to key on the ``description``, which carries a volatile
    "N times in 30 days" count — so every analysis pass stored a fresh
    near-identical row as the count grew. De-dup now keys on a stable
    structural signature, and a re-detection refreshes the existing row.
  • Aliases embedded raw entity ids (``cover.smart_garage_door_…``) instead
    of the entity's friendly name, so a reviewer couldn't tell them apart.
"""
import json
import sqlite3

import pytest


@pytest.fixture
def pa(load):
    return load("pattern_analyzer")


_SUG_SCHEMA = """
CREATE TABLE suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL, description TEXT NOT NULL, automation_yaml TEXT,
    status TEXT DEFAULT 'pending', confidence REAL DEFAULT 0.0,
    pattern_count INTEGER DEFAULT 0, approved_at TEXT, dismissed_at TEXT,
    pattern_type TEXT DEFAULT '', entity_ids TEXT DEFAULT '',
    details TEXT DEFAULT '{}'
);
"""


def _analyzer(pa, tmp_path, name="sug.db"):
    db = str(tmp_path / name)
    c = sqlite3.connect(db)
    c.executescript(_SUG_SCHEMA)
    c.commit()
    c.close()
    an = pa.PatternAnalyzer()
    an._db = db
    return an, db


def _seq_pattern(pa, trig, act, *, delay, occ, desc):
    """A trigger -> device-action sequence. ``delay`` and ``desc`` vary between
    runs; the learned automation is otherwise identical."""
    return pa.DetectedPattern(
        pattern_type="sequence", description=desc,
        entity_ids=[trig, act], confidence=min(1.0, occ / 15),
        occurrences=occ,
        details={"trigger": {"entity": trig, "state": "on"},
                 "action": {"entity": act, "state": "on"},
                 "delay_seconds": delay, "condition": None})


def _pending(db):
    c = sqlite3.connect(db)
    rows = c.execute(
        "SELECT description, automation_yaml, confidence, pattern_count "
        "FROM suggestions WHERE status='pending'").fetchall()
    c.close()
    return rows


# ── signature: stable identity, independent of count / timing / naming ───────

def test_signature_ignores_measured_timing(pa):
    # Same trigger + action, different measured lag → same signature.
    a = json.dumps({"alias": "x",
                    "trigger": {"platform": "state", "entity_id": "binary_sensor.door", "to": "on"},
                    "action": [{"delay": "00:01:00"},
                               {"service": "light.turn_on", "entity_id": "light.hall"}]})
    b = json.dumps({"alias": "totally different name",
                    "trigger": {"platform": "state", "entity_id": "binary_sensor.door", "to": "on"},
                    "action": [{"service": "light.turn_on", "entity_id": "light.hall"}]})
    assert pa.suggestion_signature(a) == pa.suggestion_signature(b) != ""


def test_signature_distinguishes_real_differences(pa):
    base = {"alias": "x",
            "trigger": {"platform": "time", "at": "07:00:00"},
            "action": {"service": "light.turn_on", "entity_id": "light.x"}}
    other_hour = dict(base, trigger={"platform": "time", "at": "23:00:00"})
    other_act = dict(base, action={"service": "light.turn_off", "entity_id": "light.x"})
    s = pa.suggestion_signature(json.dumps(base))
    assert s != pa.suggestion_signature(json.dumps(other_hour))   # different time
    assert s != pa.suggestion_signature(json.dumps(other_act))    # different action


def test_signature_empty_for_non_installable(pa):
    assert pa.suggestion_signature(json.dumps({"type": "manual_review", "note": "x"})) == ""
    assert pa.suggestion_signature("") == ""


# ── de-dup on store: the same automation is stored once ──────────────────────

def test_store_dedup_same_automation_once(pa, tmp_path):
    an, db = _analyzer(pa, tmp_path)
    p1 = _seq_pattern(pa, "binary_sensor.door", "light.hall",
                      delay=10, occ=8, desc="door->light (8 times in 30 days)")
    p2 = _seq_pattern(pa, "binary_sensor.door", "light.hall",
                      delay=60, occ=12, desc="door->light (12 times in 30 days)")
    assert an._store_suggestion(p1) is True    # first: new row
    assert an._store_suggestion(p2) is False   # second: refresh, not a new row

    rows = _pending(db)
    assert len(rows) == 1                       # only one suggestion, not two
    # the surviving row reflects the latest evidence
    assert rows[0][2] == pytest.approx(min(1.0, 12 / 15))   # confidence
    assert rows[0][3] == 12                                  # pattern_count


def test_store_keeps_distinct_automations(pa, tmp_path):
    an, db = _analyzer(pa, tmp_path)
    assert an._store_suggestion(
        _seq_pattern(pa, "binary_sensor.door", "light.hall",
                     delay=10, occ=9, desc="a")) is True
    # Different action target → a genuinely different automation.
    assert an._store_suggestion(
        _seq_pattern(pa, "binary_sensor.door", "light.porch",
                     delay=10, occ=9, desc="b")) is True
    assert len(_pending(db)) == 2


def test_store_upgrades_alias_to_friendly_name(pa, tmp_path):
    an, db = _analyzer(pa, tmp_path)
    # First stored with no name map → alias falls back to a prettified id.
    an._store_suggestion(_seq_pattern(pa, "binary_sensor.door", "light.hall",
                                      delay=10, occ=8, desc="v1"))
    # A later pass knows the friendly names → the row is refreshed in place.
    an._entity_names = {"binary_sensor.door": "Front Door", "light.hall": "Hall Light"}
    an._store_suggestion(_seq_pattern(pa, "binary_sensor.door", "light.hall",
                                      delay=10, occ=9, desc="v2"))
    rows = _pending(db)
    assert len(rows) == 1
    alias = json.loads(rows[0][1])["alias"]
    assert alias == "JARVIS Learned: Hall Light after Front Door"
    assert "binary_sensor" not in alias and "light.hall" not in alias


# ── collapsing duplicates an earlier version already stored ──────────────────

def test_dedupe_collapses_existing_duplicates(pa, tmp_path):
    an, db = _analyzer(pa, tmp_path)
    yml = an._generate_automation(
        _seq_pattern(pa, "binary_sensor.door", "light.hall",
                     delay=10, occ=8, desc="_"))
    c = sqlite3.connect(db)
    for i, conf in enumerate((0.5, 0.9, 0.7)):     # three near-identical rows
        c.execute("INSERT INTO suggestions (created, description, automation_yaml, "
                  "confidence, pattern_count, pattern_type, status) "
                  "VALUES (?,?,?,?,?, 'sequence', 'pending')",
                  (f"2026-01-0{i}", f"door->light ({8+i} times in 30 days)",
                   yml, conf, 8 + i))
    c.commit(); c.close()

    removed = an._dedupe_pending_suggestions()
    assert removed == 2
    rows = _pending(db)
    assert len(rows) == 1
    assert rows[0][2] == pytest.approx(0.9)        # kept the strongest


def test_dedupe_leaves_distinct_suggestions(pa, tmp_path):
    an, db = _analyzer(pa, tmp_path)
    an._store_suggestion(_seq_pattern(pa, "binary_sensor.door", "light.hall",
                                      delay=10, occ=9, desc="a"))
    an._store_suggestion(_seq_pattern(pa, "binary_sensor.window", "light.hall",
                                      delay=10, occ=9, desc="b"))
    assert an._dedupe_pending_suggestions() == 0
    assert len(_pending(db)) == 2


# ── friendly names in the generated alias (the reviewer-facing name) ─────────

def test_confirm_sequence_alias_uses_friendly_names(pa):
    an = pa.PatternAnalyzer()
    an._entity_names = {
        "device_tracker.sam_s_jeep": "Sam's Jeep",
        "cover.smart_garage_door_2007_garage_2": "Garage 2",
        "binary_sensor.bay_2_car_occupancy": "Car occupancy",
    }
    p = pa.DetectedPattern(
        pattern_type="confirm_sequence",
        description="⚠ ... (15 times in 30 days)",
        entity_ids=["device_tracker.sam_s_jeep",
                    "cover.smart_garage_door_2007_garage_2",
                    "binary_sensor.bay_2_car_occupancy"],
        confidence=1.0, occurrences=15,
        details={"trigger": {"entity": "device_tracker.sam_s_jeep", "state": "home"},
                 "open": {"entity": "cover.smart_garage_door_2007_garage_2", "state": "open"},
                 "close": {"entity": "cover.smart_garage_door_2007_garage_2", "state": "closed"},
                 "confirm": {"entity": "binary_sensor.bay_2_car_occupancy", "state": "on"},
                 "confirm_timeout": 120})
    alias = json.loads(an._generate_automation(p))["alias"]
    assert alias == "JARVIS Learned (review): close Garage 2 after Car occupancy confirms"
    assert "cover." not in alias and "binary_sensor." not in alias


def test_friendly_falls_back_to_prettified_object_id(pa):
    an = pa.PatternAnalyzer()          # no name map set
    assert an._friendly("switch.coffee_maker") == "Coffee Maker"
    assert an._friendly("light.hall") == "Hall"


def test_humanize_entities_swaps_known_ids_only(pa):
    text = "When device_tracker.sam_s_jeep arrives, open cover.garage_2"
    out = pa._humanize_entities(
        text, {"device_tracker.sam_s_jeep": "Sam's Jeep"})
    assert "Sam's Jeep" in out
    assert "cover.garage_2" in out     # unknown id left untouched, not dropped


# ── §7F: core signature + dismissed-suggestion resurrection prevention ───────

def _time_pattern(pa, entity, hour, minute, *, occ=12, cond=None, desc="t"):
    d = {"hour": hour, "minute": minute, "state": "on", "entity": entity}
    if cond is not None:
        d["condition"] = cond
    return pa.DetectedPattern(
        pattern_type="time_routine", description=desc, entity_ids=[entity],
        confidence=min(1.0, occ / 15), occurrences=occ, details=d)


def _set_status(db, status):
    c = sqlite3.connect(db)
    c.execute("UPDATE suggestions SET status = ?", (status,))
    c.commit(); c.close()


def _statuses(db):
    c = sqlite3.connect(db)
    rows = [r[0] for r in c.execute("SELECT status FROM suggestions").fetchall()]
    c.close()
    return rows


def test_core_signature_ignores_condition_and_mode(pa):
    base = {"alias": "x",
            "trigger": {"platform": "state", "entity_id": "binary_sensor.door", "to": "on"},
            "action": [{"service": "light.turn_on", "entity_id": "light.hall"}]}
    withcond = dict(base,
                    condition=[{"condition": "state", "entity_id": "person.sam",
                                "state": "home"}],
                    mode="restart")
    a, b = json.dumps(base), json.dumps(withcond)
    # core identity is the same despite the extra condition + mode …
    assert pa.suggestion_core_signature(a) == pa.suggestion_core_signature(b) != ""
    # … while the FULL signature (condition/mode-sensitive) still tells them apart
    assert pa.suggestion_signature(a) != pa.suggestion_signature(b)


def test_core_signature_tolerates_minute_drift_not_hour(pa):
    at2245 = {"alias": "x", "trigger": {"platform": "time", "at": "22:45:00"},
              "action": {"service": "light.turn_on", "entity_id": "light.porch"}}
    at2205 = dict(at2245, trigger={"platform": "time", "at": "22:05:00"})
    at0700 = dict(at2245, trigger={"platform": "time", "at": "07:00:00"})
    s = pa.suggestion_core_signature(json.dumps(at2245))
    assert s == pa.suggestion_core_signature(json.dumps(at2205)) != ""  # same hour
    assert s != pa.suggestion_core_signature(json.dumps(at0700))        # distinct hour


def test_dismissed_not_resurrected_on_condition_change(pa, tmp_path):
    an, db = _analyzer(pa, tmp_path)
    assert an._store_suggestion(
        _seq_pattern(pa, "binary_sensor.door", "light.hall",
                     delay=10, occ=9, desc="v1")) is True
    _set_status(db, "dismissed")                 # the user says no
    # a later pass re-learns the same behavior but with an inferred presence gate
    p2 = _seq_pattern(pa, "binary_sensor.door", "light.hall",
                      delay=10, occ=12, desc="v2")
    p2.details["condition"] = {"condition": "state",
                               "entity_id": "person.sam", "state": "home"}
    assert an._store_suggestion(p2) is False     # not re-inserted
    assert _statuses(db) == ["dismissed"]        # one row, still dismissed
    assert _pending(db) == []                    # nothing resurrected to pending


def test_dismissed_not_resurrected_on_minute_shift(pa, tmp_path):
    an, db = _analyzer(pa, tmp_path)
    assert an._store_suggestion(_time_pattern(pa, "light.porch", 22, 0, desc="v1")) is True
    _set_status(db, "dismissed")
    # §7E can re-learn the same routine at a drifted minute within the hour
    assert an._store_suggestion(_time_pattern(pa, "light.porch", 22, 45, desc="v2")) is False
    assert _statuses(db) == ["dismissed"]
    assert _pending(db) == []


def test_installed_not_resurrected(pa, tmp_path):
    an, db = _analyzer(pa, tmp_path)
    assert an._store_suggestion(_time_pattern(pa, "light.porch", 22, 0)) is True
    _set_status(db, "installed")
    assert an._store_suggestion(_time_pattern(pa, "light.porch", 22, 5)) is False
    assert _statuses(db) == ["installed"]        # not flipped back, not duplicated


def test_pending_still_refreshed_on_minute_drift(pa, tmp_path):
    an, db = _analyzer(pa, tmp_path)
    assert an._store_suggestion(_time_pattern(pa, "light.porch", 22, 0, occ=8)) is True
    assert an._store_suggestion(_time_pattern(pa, "light.porch", 22, 45, occ=14)) is False
    rows = _pending(db)
    assert len(rows) == 1
    assert rows[0][3] == 14                                   # refreshed count
    assert json.loads(rows[0][1])["trigger"]["at"] == "22:45:00"  # latest yaml


def test_cross_type_dedup_across_pattern_types(pa, tmp_path):
    """A dismissed row stored under one pattern_type (e.g. a legacy `presence`
    row) suppresses a re-learned `sequence` row with the same core identity."""
    an, db = _analyzer(pa, tmp_path)
    seq = _seq_pattern(pa, "binary_sensor.door", "light.hall",
                       delay=10, occ=9, desc="x")
    yml = an._generate_automation(seq)
    c = sqlite3.connect(db)
    c.execute("INSERT INTO suggestions (created, description, automation_yaml, "
              "confidence, pattern_count, pattern_type, status) "
              "VALUES ('2026-01-01','legacy presence row', ?, 0.8, 9, "
              "'presence', 'dismissed')", (yml,))
    c.commit(); c.close()
    assert an._store_suggestion(seq) is False    # matched across pattern_type
    assert _pending(db) == []
    assert _statuses(db) == ["dismissed"]
