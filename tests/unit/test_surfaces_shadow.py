"""Phase AA (ambient presence) — the surfaces kernel's FIRST live consumer (shadow).

`proactive_audio._emit_surfaces_shadow` is the first caller of `kernel.surfaces`,
which was a pure primitive with no live consumer. When a proactive announcement
resolves its target speakers, it logs what the cross-surface arbiter WOULD do — the
one surface that should emit, and whether the fan-out would double-announce.
Observe-only: the announcement reaches every resolved target exactly as today.
"""
import logging
import sys
import types

import pytest

from fakes import FakeHass


@pytest.fixture
def pa(load, monkeypatch):
    """Load proactive_audio with the HA module stubs it binds at import (mirrors
    test_household_shadow): config_validation / event / exceptions, forcing a fresh
    load so no partial module leaks in."""
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
    return load("proactive_audio")


def _lines(caplog):
    return [r.message for r in caplog.records
            if "surfaces(shadow): announce" in r.message]


def test_multi_target_would_double_announce(pa, caplog):
    with caplog.at_level(logging.DEBUG):
        pa._emit_surfaces_shadow(
            FakeHass(), "kitchen",
            ["media_player.kitchen", "media_player.den"], "area")
    lines = _lines(caplog)
    assert lines and "targets=2" in lines[-1] and "double_announce=True" in lines[-1]
    # All VOICE, none focused → arbiter tie-breaks on surface_id (lexicographic min).
    assert "emit on media_player.den" in lines[-1]


def test_single_target_no_double_announce(pa, caplog):
    with caplog.at_level(logging.DEBUG):
        pa._emit_surfaces_shadow(FakeHass(), "den", ["media_player.den"], "area")
    lines = _lines(caplog)
    assert lines and "targets=1" in lines[-1] and "double_announce=False" in lines[-1]
    assert "emit on media_player.den" in lines[-1]


def test_kill_switch_silences(pa, monkeypatch, caplog):
    monkeypatch.setattr(pa, "SURFACES_SHADOW", False)
    with caplog.at_level(logging.DEBUG):
        pa._emit_surfaces_shadow(
            FakeHass(), "kitchen", ["media_player.kitchen"], "area")
    assert not _lines(caplog)


def test_empty_or_blank_targets_no_log(pa, caplog):
    with caplog.at_level(logging.DEBUG):
        pa._emit_surfaces_shadow(FakeHass(), "kitchen", [], "none")
        pa._emit_surfaces_shadow(FakeHass(), "kitchen", [None, ""], "area")
    assert not _lines(caplog)
