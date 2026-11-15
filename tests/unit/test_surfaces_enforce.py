"""Phase AA (ambient presence) — cross-surface consolidation ENFORCE.

`proactive_audio._consolidate_announce_targets`: a SAME-ROOM announcement fan-out
(`mode == "area"`, more than one speaker) is consolidated to the ONE surface the
cross-surface arbiter picks — announced once, not N times. A house-wide
`broadcast` is never consolidated (house-wide by intent). DEFAULT ON
(owner-flipped, 9.128.0); kill-switched by the module flag `SURFACES_ENFORCE`
(the Governance tab) or `surfaces_enforce: false` in config, either of which
restores the full fan-out. Fail-safe to the original targets on any issue.
"""
import sys
import types

import pytest

from fakes import FakeHass


@pytest.fixture
def pa(load, monkeypatch):
    """Load proactive_audio with the HA module stubs it binds at import, and a
    controllable jarvis_config.get for the `surfaces_enforce` key."""
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

        class HomeAssistantError(Exception):
            ...

        exc.HomeAssistantError = HomeAssistantError
        monkeypatch.setitem(sys.modules, "homeassistant.exceptions", exc)
    jc = load("jarvis_config")
    store: dict = {}
    monkeypatch.setattr(jc, "get", lambda k, d=None: store.get(k, d))
    mod = load("proactive_audio")
    mod._TEST_CFG = store
    return mod


_AREA2 = ["media_player.kitchen", "media_player.den"]   # arbiter picks "…den"


def test_on_by_default(pa):
    # Owner-flipped 9.128.0: ships ON with nothing configured.
    assert pa.SURFACES_ENFORCE is True
    assert pa._surfaces_enforce_on() is True


def test_kill_switch_flag_reverts(pa, monkeypatch):
    # The Governance tab sets the module flag; off is decisive.
    monkeypatch.setattr(pa, "SURFACES_ENFORCE", False)
    assert pa._surfaces_enforce_on() is False
    # …even if the config key says true — the flag is the master kill-switch.
    pa._TEST_CFG["surfaces_enforce"] = True
    assert pa._surfaces_enforce_on() is False


@pytest.mark.parametrize("off", [False, "false", "off", "0", "no", " False "])
def test_kill_switch_config_reverts(pa, off):
    pa._TEST_CFG["surfaces_enforce"] = off
    assert pa._surfaces_enforce_on() is False


@pytest.mark.parametrize("on", [True, "true", 1, None])
def test_config_non_false_keeps_default_on(pa, on):
    pa._TEST_CFG["surfaces_enforce"] = on
    assert pa._surfaces_enforce_on() is True


def test_killed_leaves_fanout_unchanged(pa, monkeypatch):
    # Kill-switch off: a same-room fan-out is untouched (the pre-flip behaviour).
    monkeypatch.setattr(pa, "SURFACES_ENFORCE", False)
    assert pa._consolidate_announce_targets(FakeHass(), "kitchen", _AREA2, "area") == _AREA2
    monkeypatch.setattr(pa, "SURFACES_ENFORCE", True)
    pa._TEST_CFG["surfaces_enforce"] = False
    assert pa._consolidate_announce_targets(FakeHass(), "kitchen", _AREA2, "area") == _AREA2


def test_area_fanout_consolidated_by_default(pa):
    out = pa._consolidate_announce_targets(FakeHass(), "kitchen", _AREA2, "area")
    # Consolidated to the ONE arbiter pick (all VOICE, none focused → min surface_id).
    assert out == ["media_player.den"]


def test_broadcast_never_consolidated(pa):
    out = pa._consolidate_announce_targets(FakeHass(), "kitchen", _AREA2, "broadcast")
    assert out == _AREA2   # house-wide by intent — every speaker kept


def test_single_target_unchanged(pa):
    out = pa._consolidate_announce_targets(FakeHass(), "den", ["media_player.den"], "area")
    assert out == ["media_player.den"]


def test_failsafe_on_blank_targets(pa):
    # Blanks can't arbitrate to a target in the list → fail-safe to the original.
    assert pa._consolidate_announce_targets(FakeHass(), "x", [None, ""], "area") == [None, ""]
