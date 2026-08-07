"""Tests for HA activity history (v6.72.0). Integration-boundary code (recorder
+ logbook), so we test the pure logic that carries correctness — entity/area
resolution, timeline shaping, count computation, window clamping, and the
defensive fallbacks (a recorder miss returns empty, never raises, never
fabricates) — with the HA components stubbed."""
import importlib.util
import pathlib
import sys
import types
from datetime import datetime, timezone

import pytest

COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"


@pytest.fixture
def ah(monkeypatch):
    """Load activity_history with stubbed HA recorder/logbook/util modules so its
    imports resolve. All via monkeypatch so nothing leaks to other test files."""
    if "jc" not in sys.modules:
        pkg = types.ModuleType("jc")
        pkg.__path__ = [str(COMP)]
        monkeypatch.setitem(sys.modules, "jc", pkg)

    # --- stub homeassistant.util.dt ---
    dt_mod = types.ModuleType("homeassistant.util.dt")
    dt_mod.utcnow = lambda: datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setitem(sys.modules, "homeassistant", types.ModuleType("homeassistant"))
    monkeypatch.setitem(sys.modules, "homeassistant.util", types.ModuleType("homeassistant.util"))
    monkeypatch.setitem(sys.modules, "homeassistant.util.dt", dt_mod)

    # --- stub recorder.get_instance + history ---
    rec_mod = types.ModuleType("homeassistant.components.recorder")

    class _Instance:
        async def async_add_executor_job(self, fn, *a):
            return fn(*a)
    rec_mod.get_instance = lambda hass: _Instance()

    hist_mod = types.SimpleNamespace()
    # default: no data; individual tests override hass._history
    hist_mod.get_significant_states = lambda hass, start, end, ids, **kw: getattr(hass, "_history", {})
    rec_mod.history = hist_mod
    monkeypatch.setitem(sys.modules, "homeassistant.components", types.ModuleType("homeassistant.components"))
    monkeypatch.setitem(sys.modules, "homeassistant.components.recorder", rec_mod)

    # --- stub logbook ---
    lb_mod = types.ModuleType("homeassistant.components.logbook")
    lb_mod.async_get_events = lambda hass, start, end, entity_ids=None: getattr(hass, "_logbook", [])
    monkeypatch.setitem(sys.modules, "homeassistant.components.logbook", lb_mod)

    key = "jc.activity_history"
    monkeypatch.delitem(sys.modules, key, raising=False)
    spec = importlib.util.spec_from_file_location(key, COMP / "activity_history.py")
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, key, mod)
    spec.loader.exec_module(mod)
    return mod


class _State:
    def __init__(self, eid, state, fn=None, when=None):
        self.entity_id = eid
        self.state = state
        self.attributes = {"friendly_name": fn} if fn else {}
        self.last_changed = when
        self.last_updated = when


class _Hass:
    def __init__(self, states=None, history=None, logbook=None):
        self._states = states or []
        self._history = history or {}
        self._logbook = logbook or []
    class _S:
        pass
    @property
    def states(self):
        s = _Hass._S()
        s.get = lambda eid: next((x for x in self._states if x.entity_id == eid), None)
        s.async_all = lambda: self._states
        return s


# ── entity resolution ────────────────────────────────────────────────────────

def test_resolve_exact_entity_id(ah):
    hass = _Hass([_State("binary_sensor.front_door", "off")])
    assert ah._resolve_entities(hass, "binary_sensor.front_door", None) == ["binary_sensor.front_door"]


def test_resolve_by_friendly_name(ah):
    hass = _Hass([
        _State("binary_sensor.fd", "off", fn="Front Door"),
        _State("light.k", "on", fn="Kitchen Light"),
    ])
    out = ah._resolve_entities(hass, "front door", None)
    assert "binary_sensor.fd" in out
    assert "light.k" not in out


def test_resolve_unknown_returns_empty(ah):
    hass = _Hass([_State("light.k", "on", fn="Kitchen")])
    assert ah._resolve_entities(hass, "spaceship", None) == []


# ── entity history: timeline + counts ────────────────────────────────────────

async def test_entity_history_builds_timeline_and_counts(ah):
    when1 = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)
    when2 = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
    hist = {"binary_sensor.fd": [
        _State("binary_sensor.fd", "on", when=when1),
        _State("binary_sensor.fd", "off", when=when2),
    ]}
    hass = _Hass([_State("binary_sensor.fd", "off", fn="Front Door")], history=hist)
    res = await ah.entity_history(hass, entity="front door", hours=24)
    assert res["counts"]["binary_sensor.fd"] == 2
    assert len(res["timeline"]) == 2
    # newest first
    assert res["timeline"][0]["state"] == "off"
    assert res["timeline"][0]["when"].startswith("2026-08-03T10:00")


async def test_entity_history_no_match_is_error(ah):
    hass = _Hass([_State("light.k", "on", fn="Kitchen")])
    res = await ah.entity_history(hass, entity="nonexistent", hours=24)
    assert "error" in res


async def test_entity_history_empty_window_notes_it(ah):
    hass = _Hass([_State("binary_sensor.fd", "off", fn="Front Door")], history={})
    res = await ah.entity_history(hass, entity="front door", hours=24)
    assert res["timeline"] == []
    assert "note" in res


async def test_entity_history_clamps_hours(ah):
    # absurd hours should clamp, not error
    hass = _Hass([_State("binary_sensor.fd", "off", fn="Front Door")],
                 history={"binary_sensor.fd": []})
    res = await ah.entity_history(hass, entity="front door", hours=999999)
    assert res["hours"] <= 24 * 30


async def test_entity_history_recorder_unavailable(ah, monkeypatch):
    # if the recorder import fails, return a clean error (not a crash)
    monkeypatch.setitem(sys.modules, "homeassistant.components.recorder", None)
    hass = _Hass([_State("binary_sensor.fd", "off", fn="Front Door")])
    res = await ah.entity_history(hass, entity="front door", hours=24)
    assert "error" in res


# ── logbook ──────────────────────────────────────────────────────────────────

async def test_logbook_returns_entries(ah):
    lb = [
        {"when": "2026-08-03T09:00:00Z", "name": "Front Door", "message": "opened",
         "entity_id": "binary_sensor.fd"},
        {"when": "2026-08-03T09:05:00Z", "name": "JARVIS", "message": "armed lockdown"},
    ]
    hass = _Hass(logbook=lb)
    res = await ah.logbook(hass, hours=24)
    assert len(res["entries"]) == 2
    assert res["entries"][0]["name"] == "Front Door"
    assert res["entries"][1]["message"] == "armed lockdown"


async def test_logbook_empty_notes_it(ah):
    hass = _Hass(logbook=[])
    res = await ah.logbook(hass, hours=24)
    assert res["entries"] == []
    assert "note" in res


async def test_logbook_clamps_hours(ah):
    hass = _Hass(logbook=[])
    res = await ah.logbook(hass, hours=999999)
    assert res["hours"] <= 24 * 14
