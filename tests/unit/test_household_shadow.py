"""Phase P shadow — proactive_audio folds each audit tick's occupancy sample into
the kernel household model and logs what it WOULD anticipate (occupancy rhythm +
routine graph), beside the live PredictiveHabitMatrix.

`_emit_household_shadow` is observe-only: the household model's suggestions carry
no actuator, so nothing here acts. These tests pin the binder — the observation it
builds from a tick, the bounded rolling window, the routine-mining activity label,
the two kill-switches, and that it never raises into the audit. The household
model's own derivations (rhythm / routines / anticipate) are covered in
test_kernel_household.
"""
import sys
import types

import pytest


@pytest.fixture
def pa(load, monkeypatch):
    """Load proactive_audio with the HA module stubs it imports at module level.

    proactive_audio binds ``area_registry``/``config_validation``/``event`` at
    import and the loader caches the module, so a stale or incomplete stub would
    leak into later tests. We rely on conftest's complete base ``area_registry``
    stub (no override here) and force proactive_audio to (re)load fresh, popping it
    again on teardown so the next test rebinds its own stubs — no cross-test leak.
    """
    sys.modules.pop("jc.proactive_audio", None)
    helpers = sys.modules.setdefault(
        "homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
    cv = types.ModuleType("homeassistant.helpers.config_validation")
    cv.string, cv.boolean = str, bool
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.config_validation", cv)
    monkeypatch.setattr(helpers, "config_validation", cv, raising=False)
    ev = types.ModuleType("homeassistant.helpers.event")
    ev.async_call_later = lambda *a, **k: (lambda: None)
    ev.async_track_time_interval = lambda *a, **k: (lambda: None)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.event", ev)
    monkeypatch.setattr(helpers, "event", ev, raising=False)
    if "homeassistant.exceptions" not in sys.modules:
        exc = types.ModuleType("homeassistant.exceptions")
        class HomeAssistantError(Exception): ...
        exc.HomeAssistantError = HomeAssistantError
        monkeypatch.setitem(sys.modules, "homeassistant.exceptions", exc)
    mod = load("proactive_audio")
    yield mod
    sys.modules.pop("jc.proactive_audio", None)


# ── the observation the binder builds from a tick ────────────────────────────
def test_shadow_appends_observation_for_occupied(pa):
    entry: dict = {}
    pa._emit_household_shadow(entry, ["kitchen", "bedroom"])
    window = entry["_household_window"]
    assert len(window) == 1
    obs = window[0]
    assert obs.occupied is True
    # the activity is the first area alphabetically — the routine-mining label
    assert obs.activity == "bedroom"
    assert obs.daypart  # a non-empty daypart was injected from the clock
    assert entry["_household_last_activity"] == "bedroom"


def test_shadow_empty_home_is_away(pa):
    entry: dict = {}
    pa._emit_household_shadow(entry, [])
    obs = entry["_household_window"][0]
    assert obs.occupied is False
    assert obs.activity == "away"


def test_shadow_daypart_follows_the_clock(pa, monkeypatch):
    # pin the clock to a morning hour and assert the injected daypart matches the
    # kernel's own vocabulary (space_time.daypart_of), not an ad-hoc bucket.
    import datetime as _dt
    from jc.kernel import space_time
    fixed = _dt.datetime(2026, 1, 5, 8, 30)   # Monday 08:30 → morning
    monkeypatch.setattr("homeassistant.util.dt.now", lambda tz=None: fixed)
    entry: dict = {}
    pa._emit_household_shadow(entry, ["office"])
    obs = entry["_household_window"][0]
    assert obs.daypart == space_time.daypart_of(8)
    assert obs.weekday == 0   # Monday


def test_shadow_window_is_bounded(pa):
    entry: dict = {}
    for _ in range(pa._HOUSEHOLD_WINDOW_MAX + 50):
        pa._emit_household_shadow(entry, ["kitchen"])
    assert len(entry["_household_window"]) == pa._HOUSEHOLD_WINDOW_MAX


def test_shadow_tracks_last_activity_across_ticks(pa):
    entry: dict = {}
    pa._emit_household_shadow(entry, ["kitchen"])
    assert entry["_household_last_activity"] == "kitchen"
    pa._emit_household_shadow(entry, ["garage"])
    assert entry["_household_last_activity"] == "garage"
    assert len(entry["_household_window"]) == 2


# ── kill-switches ────────────────────────────────────────────────────────────
def test_shadow_kill_switch_module_flag(pa, monkeypatch):
    monkeypatch.setattr(pa, "HOUSEHOLD_SHADOW", False)
    entry: dict = {}
    pa._emit_household_shadow(entry, ["kitchen"])
    assert "_household_window" not in entry


def test_shadow_kill_switch_config_key(pa, monkeypatch):
    import jc.jarvis_config as cfg
    monkeypatch.setattr(
        cfg, "get",
        lambda key, default=None: False if key == "household_shadow" else default)
    entry: dict = {}
    pa._emit_household_shadow(entry, ["kitchen"])
    assert "_household_window" not in entry


# ── defensive: never raises into the audit tick ──────────────────────────────
def test_shadow_never_raises_on_bad_input(pa):
    # a non-iterable occupancy sample must be swallowed, not propagated
    pa._emit_household_shadow({}, 12345)  # should not raise


def test_shadow_never_raises_on_bad_entry(pa):
    # entry_data that rejects setdefault must be swallowed too
    class _Bad:
        def setdefault(self, *a, **k):
            raise RuntimeError("boom")
        def get(self, *a, **k):
            return None
    pa._emit_household_shadow(_Bad(), ["kitchen"])  # should not raise
