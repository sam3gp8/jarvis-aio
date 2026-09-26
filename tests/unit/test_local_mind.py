"""Tests for local-mind history, decision, and verbalization behavior."""
import gc
import sqlite3
import sys
import types

import pytest


def _close_sqlite_connections():
    connections = [
        obj for obj in gc.get_objects() if isinstance(obj, sqlite3.Connection)
    ]
    for conn in connections:
        try:
            conn.close()
        except Exception:
            pass
    gc.collect()


@pytest.fixture
def mind(load, monkeypatch):
    module = load("local_mind")
    monkeypatch.setattr(module, "_hist_cache", {})
    monkeypatch.setattr(module, "_days_cache", (0.0, 0.0))
    monkeypatch.setattr(module, "_recent_events", {})
    monkeypatch.setattr(module, "_stats", {"decisions": 0, "spoke": 0, "silent": 0})

    persona = types.ModuleType("jc.persona")
    persona.__dict__.update({
        "register_for": lambda urgency: "neutral",
        "announce_opener": lambda honorific, register: f"{honorific.title()},",
    })
    monkeypatch.setitem(sys.modules, "jc.persona", persona)
    monkeypatch.setattr(sys.modules["jc"], "persona", persona, raising=False)
    return module


def _decision(mind, **changes):
    args = {
        "honorific": "sir",
        "entity_id": "sensor.test_event",
        "domain": "sensor",
        "device_class": "",
        "category": "other",
        "from_state": "off",
        "to_state": "on",
        "friendly_name": "Test event",
        "urgency": "medium",
        "anyone_home": True,
        "recent_announcements": [],
        "hour": 12,
        "history": {"grade": "unknown", "days": 0, "at_hour": 0, "total": 0},
        "prior": (0, 0),
    }
    args.update(changes)
    return mind.assess_core(**args)


def _create_history_db(path, rows):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE state_changes "
            "(entity_id TEXT, new_state TEXT, hour INTEGER, timestamp TEXT)")
        conn.executemany(
            "INSERT INTO state_changes VALUES (?, ?, ?, ?)", rows)


def test_history_profile_classifies_all_observed_grades(mind, tmp_path, monkeypatch):
    db_path = tmp_path / "history.db"
    rows = []
    for day in range(1, 11):
        stamp = f"2026-01-{day:02d} 12:00:00"
        rows.append(("sensor.unusual", "on", 12, stamp))
    rows.extend([
        ("sensor.occasional", "on", 8, "2026-01-01 08:00:00"),
        ("sensor.common", "on", 8, "2026-01-01 08:00:00"),
        ("sensor.common", "on", 9, "2026-01-02 09:00:00"),
    ])
    rows.extend(
        ("sensor.routine", "on", 8, f"2026-01-{day:02d} 08:00:00")
        for day in range(1, 6))
    _create_history_db(db_path, rows)
    monkeypatch.setattr(mind, "DB_PATH", str(db_path))

    assert mind.history_profile("sensor.novel", "on", 8)["grade"] == "novel"
    assert mind.history_profile("sensor.unusual", "on", 20)["grade"] == "unusual_hour"
    assert mind.history_profile("sensor.occasional", "on", 8)["grade"] == "occasional"
    assert mind.history_profile("sensor.common", "on", 8)["grade"] == "common"
    assert mind.history_profile("sensor.routine", "on", 8)["grade"] == "routine"


def test_history_profile_short_history_unknown_and_cached(mind, tmp_path, monkeypatch):
    db_path = tmp_path / "short-history.db"
    _create_history_db(db_path, [
        ("sensor.door", "on", 2, "2026-01-01 02:00:00"),
        ("sensor.door", "on", 2, "2026-01-03 02:00:00"),
    ])
    monkeypatch.setattr(mind, "DB_PATH", str(db_path))
    profile = mind.history_profile("sensor.door", "on", 2)
    assert profile == {"days": 2.0, "at_hour": 0, "total": 0, "grade": "unknown"}

    monkeypatch.setattr(mind, "_data_days", lambda: (_ for _ in ()).throw(AssertionError()))
    assert mind.history_profile("sensor.door", "on", 2) is profile


def test_history_profile_handles_unavailable_or_broken_database(mind, monkeypatch):
    monkeypatch.setattr(mind, "_connect", lambda: None)
    assert mind.history_profile("sensor.no_db", "on", 2)["grade"] == "unknown"

    class BrokenConnection:
        closed = False

        def execute(self, query):
            raise sqlite3.OperationalError("missing table")

        def close(self):
            self.closed = True

    conn = BrokenConnection()
    monkeypatch.setattr(mind, "_connect", lambda: conn)
    monkeypatch.setattr(mind, "_data_days", lambda: 5.0)
    result = mind.history_profile("sensor.broken", "on", 2)
    assert result["grade"] == "unknown"
    assert conn.closed is True


def test_connect_failure_and_empty_history_database(mind, tmp_path, monkeypatch):
    db_path = tmp_path / "empty-history.db"
    _create_history_db(db_path, [])
    monkeypatch.setattr(mind, "DB_PATH", str(db_path))

    with monkeypatch.context() as patch:
        patch.setattr(
            mind.sqlite3, "connect",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                sqlite3.OperationalError("locked")))
        assert mind._connect() is None

    assert mind._data_days() == 0.0
    assert mind._days_cache[1] == 0.0

    broken_path = tmp_path / "missing-table.db"
    with sqlite3.connect(broken_path) as conn:
        pass
    monkeypatch.setattr(mind, "DB_PATH", str(broken_path))
    monkeypatch.setattr(mind, "_days_cache", (0.0, 0.0))
    assert mind._data_days() == 0.0


def test_history_cache_evicts_old_entries_after_limit(mind, monkeypatch):
    monkeypatch.setattr(mind, "_data_days", lambda: 0.0)
    mind._hist_cache.update({
        ("sensor.old", str(index), 0): (0.0, {}) for index in range(800)})

    mind.history_profile("sensor.new", "on", 0)

    assert len(mind._hist_cache) == 601
    assert ("sensor.old", "0", 0) not in mind._hist_cache
    assert ("sensor.old", "200", 0) in mind._hist_cache

    _close_sqlite_connections()


def test_note_event_detects_flapping_and_expires_old_events(mind, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(mind.time, "monotonic", lambda: now[0])
    assert mind._note_event("sensor.door") is False
    now[0] += 1
    assert mind._note_event("sensor.door") is False
    now[0] += 1
    assert mind._note_event("sensor.door") is True
    now[0] += mind._FLAP_WINDOW + 1
    assert mind._note_event("sensor.door") is False


def test_duplicate_detection_uses_friendly_entity_and_recent_window(mind):
    assert mind._is_duplicate(
        "Front Door", "binary_sensor.front_door", ["The front door is open"])
    assert mind._is_duplicate(
        "", "lock.garage_lock", ["Garage lock was secured"])
    assert not mind._is_duplicate("", "", ["anything happened"])
    assert not mind._is_duplicate(
        "Front Door", "binary_sensor.front_door",
        ["front door event"] * 6 + ["unrelated"] * 6)


@pytest.mark.parametrize(("domain", "device_class", "state", "entity", "expected"), [
    ("sensor", "door", "on", "sensor.entry", True),
    ("cover", "", "open", "cover.garage", True),
    ("sensor", "", "unlocked", "sensor.side_gate", True),
    ("sensor", "", "off", "sensor.front_door", False),
    ("sensor", "", "on", "sensor.temperature", False),
])
def test_security_relevance_requires_opening_state(
        mind, domain, device_class, state, entity, expected):
    assert mind._security_relevant(domain, device_class, state, entity) is expected


def test_case_prior_returns_memory_and_degrades_on_error(mind, monkeypatch):
    cache = types.ModuleType("jc.reasoning_cache")
    cache.__dict__["similar"] = lambda *args: (3, 0)
    monkeypatch.setitem(sys.modules, "jc.reasoning_cache", cache)
    monkeypatch.setattr(sys.modules["jc"], "reasoning_cache", cache, raising=False)
    assert mind._case_prior("lock", "door", "security", False) == (3, 0)

    cache.__dict__["similar"] = lambda *args: (_ for _ in ()).throw(
        RuntimeError("cache unavailable"))
    assert mind._case_prior("lock", "door", "security", False) == (0, 0)


@pytest.mark.parametrize(("state", "device_class", "entity_id", "expected"), [
    ("on", "door", "binary_sensor.door", "is open"),
    ("off", "window", "binary_sensor.window", "is closed"),
    ("on", "motion", "binary_sensor.hall", "has detected motion"),
    ("off", "occupancy", "binary_sensor.room", "has gone quiet"),
    ("on", "smoke", "binary_sensor.smoke", "is detecting smoke"),
    ("off", "gas", "binary_sensor.gas", "has cleared"),
    ("on", "", "binary_sensor.water_leak", "is detecting water"),
    ("55", "battery", "sensor.remote", "battery is at 55%"),
    ("55.5", "", "sensor.temperature", "reads 55.5"),
    ("locked", "", "lock.front_door", "is locked"),
    ("", "", "", "needs attention"),
])
def test_state_phrase_is_device_aware(
        mind, state, device_class, entity_id, expected):
    assert mind._state_phrase(state, device_class, entity_id) == expected


def test_compose_announcement_adds_context_and_stable_variant(mind):
    first = mind.compose_announcement(
        "sir", "Front door", "binary_sensor.front_door", "on", "door",
        hour=2, novelty="novel", away=True)
    second = mind.compose_announcement(
        "sir", "Front door", "binary_sensor.front_door", "on", "door",
        hour=2, novelty="novel", away=True)

    assert first == second
    assert first.startswith("Sir, Front door is open")
    assert "while no one is home" in first
    assert any(phrase in first for phrase in (
        "the first time I've observed this", "I haven't seen this before",
        "a first in my records"))
    assert "at this hour of the night" in mind.compose_announcement(
        "sir", "Side window", "binary_sensor.side_window", "on", "window",
        hour=2, novelty="unusual_hour")
    assert "out of pattern for this time of day" in mind.compose_announcement(
        "sir", "Side window", "binary_sensor.side_window", "on", "window",
        hour=14, novelty="unusual_hour")


def test_compose_uses_fallback_opener_when_persona_is_unavailable(mind, monkeypatch):
    monkeypatch.setitem(sys.modules, "jc.persona", None)
    monkeypatch.delattr(sys.modules["jc"], "persona", raising=False)
    message = mind.compose_announcement("sir", "Hall light", "light.hall", "on", hour=8)
    assert message == "Sir, Hall light is on."


def test_compose_defaults_to_current_hour(mind):
    message = mind.compose_announcement("sir", "Hall light", "light.hall", "on")
    assert message == "Sir, Hall light is on."


def test_compose_falls_back_when_persona_opener_raises(mind, monkeypatch):
    persona = types.ModuleType("jc.persona")
    persona.__dict__.update({
        "register_for": lambda urgency: "neutral",
        "announce_opener": lambda *args: (_ for _ in ()).throw(
            RuntimeError("persona unavailable")),
    })
    monkeypatch.setitem(sys.modules, "jc.persona", persona)
    monkeypatch.setattr(sys.modules["jc"], "persona", persona, raising=False)

    assert mind.compose_announcement(
        "sir", "Hall light", "light.hall", "on", hour=8) == "Sir, Hall light is on."


def test_assess_core_prioritizes_critical_duplicate_and_flapping(mind):
    critical = _decision(
        mind, urgency="critical", recent_announcements=["Test event was already reported"])
    assert critical["speak"] is True
    assert critical["urgency"] == "critical"
    assert "critical urgency" in critical["reason"]

    duplicate = _decision(
        mind, entity_id="sensor.duplicate", friendly_name="Duplicate",
        recent_announcements=["Duplicate is on"])
    assert duplicate["speak"] is False
    assert "already announced" in duplicate["reason"]

    results = [
        _decision(mind, entity_id="sensor.flap", friendly_name="Flap")
        for _ in range(mind._FLAP_COUNT)
    ]
    assert results[-1]["speak"] is False
    assert "flapping" in results[-1]["reason"]


@pytest.mark.parametrize(("changes", "speak", "urgency", "reason_part"), [
    ({"urgency": "high"}, True, "high", "high urgency"),
    ({"urgency": "medium", "anyone_home": False,
      "domain": "binary_sensor", "device_class": "door", "to_state": "on"},
     True, "high", "while away"),
    ({"urgency": "medium", "anyone_home": True,
      "history": {"grade": "novel"}}, True, "medium", "novel event"),
    ({"urgency": "medium", "history": {"grade": "unusual_hour", "total": 4}},
     True, "medium", "hourly pattern"),
    ({"urgency": "medium", "history": {"grade": "routine", "at_hour": 8, "days": 10}},
     False, "medium", "routine at this hour"),
    ({"urgency": "medium", "history": {"grade": "common", "at_hour": 3, "days": 10}},
     False, "medium", "common at this hour"),
    ({"urgency": "medium", "history": {"grade": "occasional"}, "anyone_home": False},
     True, "medium", "while away"),
    ({"urgency": "medium", "history": {"grade": "unknown"}, "anyone_home": True},
     False, "medium", "while home"),
    ({"urgency": "low"}, False, "low", "low urgency"),
    ({"urgency": "medium", "prior": (0, 2)}, False, "medium", "case memory"),
    ({"urgency": "medium", "prior": (2, 0)}, True, "medium", "case memory"),
])
def test_assess_core_decision_matrix(mind, changes, speak, urgency, reason_part):
    result = _decision(mind, entity_id=f"sensor.case_{reason_part.replace(' ', '_')}", **changes)
    assert result["speak"] is speak
    assert result["urgency"] == urgency
    assert reason_part in result["reason"]
    if speak:
        assert result["message"].endswith(".")
    else:
        assert "message" not in result


@pytest.mark.asyncio
async def test_assess_runs_history_and_case_lookup_in_executor(mind, fake_hass, monkeypatch):
    history_calls = []

    def history(entity_id, to_state, hour):
        history_calls.append((entity_id, to_state, hour))
        return {"grade": "novel", "days": 4, "at_hour": 0, "total": 0}

    monkeypatch.setattr(mind, "history_profile", history)
    monkeypatch.setattr(mind, "_case_prior", lambda *args: (0, 0))

    result = await mind.assess(
        fake_hass, honorific="sir", entity_id="sensor.window",
        domain="binary_sensor", device_class="window", category="security",
        to_state="on", friendly_name="Window", urgency="medium", anyone_home=True)

    assert history_calls[0][:2] == ("sensor.window", "on")
    assert result["speak"] is True
    assert "novel event" in result["reason"]


@pytest.mark.asyncio
async def test_assess_falls_back_when_history_lookup_raises(mind, fake_hass, monkeypatch):
    def fail_history(*args):
        raise RuntimeError("database busy")

    monkeypatch.setattr(mind, "history_profile", fail_history)
    monkeypatch.setattr(mind, "_case_prior", lambda *args: (0, 0))

    result = await mind.assess(
        fake_hass, honorific="sir", entity_id="sensor.status", to_state="on",
        urgency="medium", anyone_home=True)

    assert result["speak"] is False
    assert "unknown while home" in result["reason"]


@pytest.mark.asyncio
async def test_assess_continues_when_optional_websocket_logger_is_missing(
        mind, fake_hass, monkeypatch):
    monkeypatch.setitem(sys.modules, "jc.websocket", None)
    monkeypatch.delattr(sys.modules["jc"], "websocket", raising=False)
    monkeypatch.setattr(
        mind, "history_profile",
        lambda *args: {"grade": "novel", "days": 0, "at_hour": 0, "total": 0})
    monkeypatch.setattr(mind, "_case_prior", lambda *args: (0, 0))

    result = await mind.assess(
        fake_hass, honorific="sir", entity_id="sensor.window",
        to_state="open", urgency="medium", anyone_home=True)

    assert result["speak"] is True
    assert "novel event" in result["reason"]


def test_stats_returns_a_copy_of_decision_counters(mind):
    _decision(mind, urgency="high", entity_id="sensor.spoken")
    _decision(mind, urgency="low", entity_id="sensor.silent")

    result = mind.stats()
    assert result == {"decisions": 2, "spoke": 1, "silent": 1}
    result["decisions"] = 100
    assert mind.stats()["decisions"] == 2