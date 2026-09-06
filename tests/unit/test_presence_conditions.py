"""state (presence) conditions on time routines — "only when the owner is home".

A time trigger has no inherent presence, so gating a learned routine on the
owner's person entity is a real guard (unlike motion sequences, where presence
is implied). The owner is resolved to a person.* entity via a name→entity map
built from hass; the routine's description already reflects the observation, and
now an HA state condition enforces it.
"""
import json
import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def pa(load):
    return load("pattern_analyzer")


_SCHEMA = (
    "CREATE TABLE state_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "timestamp TEXT, entity_id TEXT, domain TEXT, old_state TEXT, "
    "new_state TEXT, area_id TEXT, hour INTEGER, day_of_week INTEGER, "
    "person TEXT, person_confidence REAL)")


def _conn(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_owned_routine(conn, owner="sam"):
    # light.porch turns on ~19:00 on many days, attributed to `owner`
    base = datetime.now() - timedelta(days=20)
    for d in range(15):
        t = base + timedelta(days=d, hours=19)
        conn.execute("INSERT INTO state_changes (timestamp, entity_id, domain, "
                     "old_state, new_state, hour, day_of_week, person, "
                     "person_confidence) VALUES (?,?,?,?,?,?,?,?,?)",
                     (t.isoformat(), "light.porch", "light", "off", "on",
                      19, t.weekday(), owner, 0.9))
    conn.commit()


def test_time_routine_gets_presence_condition(pa, tmp_path):
    conn = _conn(tmp_path / "r.db")
    _seed_owned_routine(conn, owner="sam")
    person_map = {"sam": "person.sam"}
    pats = pa.PatternAnalyzer()._find_time_routines(conn, person_map)
    m = [p for p in pats if p.entity_ids == ["light.porch"]]
    assert m, "expected the porch routine"
    cond = m[0].details.get("condition")
    assert cond == {"condition": "state", "entity_id": "person.sam", "state": "home"}
    assert m[0].details.get("person") == "sam"


def test_time_routine_no_condition_when_owner_unresolved(pa, tmp_path):
    conn = _conn(tmp_path / "r2.db")
    _seed_owned_routine(conn, owner="sam")
    # empty map → owner can't be resolved to a person entity → no condition,
    # but the routine is still detected.
    pats = pa.PatternAnalyzer()._find_time_routines(conn, {})
    m = [p for p in pats if p.entity_ids == ["light.porch"]]
    assert m and m[0].details.get("condition") is None


def test_generate_time_routine_emits_presence_condition(pa):
    p = pa.DetectedPattern(
        pattern_type="time_routine", description="x",
        entity_ids=["light.porch"], confidence=0.9, occurrences=15,
        details={"hour": 19, "state": "on",
                 "condition": {"condition": "state",
                               "entity_id": "person.sam", "state": "home"}})
    auto = json.loads(pa.PatternAnalyzer()._generate_automation(p))
    assert auto["trigger"]["platform"] == "time"
    assert auto["condition"] == [{"condition": "state",
                                  "entity_id": "person.sam", "state": "home"}]


def test_condition_phrase_state(pa):
    assert pa._condition_phrase(
        {"condition": "state", "entity_id": "person.sam", "state": "home"}
    ) == ", only when person.sam is home"


class _FakeState:
    def __init__(self, entity_id, friendly):
        self.entity_id = entity_id
        self.attributes = {"friendly_name": friendly}


class _FakeStates:
    def __init__(self, people):
        self._people = people

    def async_all(self, domain):
        return self._people if domain == "person" else []


class _FakeHass:
    def __init__(self, people):
        self.states = _FakeStates(people)


def test_person_entity_map_resolves_normalized_and_friendly(pa):
    hass = _FakeHass([_FakeState("person.sam_smith", "Sam Smith")])
    m = pa.PatternAnalyzer()._person_entity_map(hass)
    # a routine's owner may be recorded normalized ("sam_smith"), as the friendly
    # name ("Sam Smith"), or as the entity id — all must resolve.
    assert m["sam_smith"] == "person.sam_smith"
    assert m["Sam Smith"] == "person.sam_smith"
    assert m["person.sam_smith"] == "person.sam_smith"
