"""The deterministic fast-path must honor the authorization gate — v7.44.0.

local_engine.try_local intercepts simple commands before the LLM. Protected
actions (unlock, open a garage/cover) must not be a second, unguarded way to
actuate: when confirmation is enabled they defer to the agent (which runs the
gate + prompt); when it's off they actuate fast, exactly as the agent would.
"""
from __future__ import annotations

import sys
import types

import pytest

from fakes import FakeHass


def _install_vc(monkeypatch, protected: bool):
    """Fake voice_confirm.action_is_protected mirroring the real one: protected
    services (unlock / open a cover) require confirmation only when enabled;
    everything else (lights, etc.) never does. `protected` here means 'is
    confirmation enabled'."""
    _SENSITIVE = {("lock", "unlock"), ("cover", "open_cover"),
                  ("cover", "open"), ("alarm_control_panel", "alarm_disarm")}
    fake = types.ModuleType("jc.voice_confirm")
    fake.action_is_protected = lambda hass, d, s, e="": protected and (d, s) in _SENSITIVE
    async def _confirm(hass, q, entity_id=""):
        return False
    fake.confirm = _confirm
    monkeypatch.setitem(sys.modules, "jc.voice_confirm", fake)
    monkeypatch.setattr(sys.modules["jc"], "voice_confirm", fake, raising=False)


# ── _service_for (pure mapping) ───────────────────────────────────────────────
def test_service_for_maps_actions(load):
    le = load("local_engine")
    assert le._service_for("unlock", "lock.front") == ("lock", "unlock")
    assert le._service_for("open", "cover.garage") == ("cover", "open_cover")
    assert le._service_for("turn_off", "light.kitchen") == ("light", "turn_off")
    assert le._service_for("turn_on", "switch.pump") == ("switch", "turn_on")
    assert le._service_for("media_pause", "media_player.tv") == ("media_player", "media_pause")
    assert le._service_for("greeting", "") is None


# ── _needs_confirmation ───────────────────────────────────────────────────────
def test_needs_confirmation_true_for_protected_when_enabled(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch, protected=True)
    assert le._needs_confirmation(None, "unlock", "lock.front") is True
    assert le._needs_confirmation(None, "open", "cover.garage") is True


def test_needs_confirmation_false_when_disabled(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch, protected=False)
    assert le._needs_confirmation(None, "unlock", "lock.front") is False


def test_needs_confirmation_false_for_non_actuation(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch, protected=True)      # even if "protected" says yes,
    assert le._needs_confirmation(None, "greeting", "") is False   # no service to gate


# ── try_local end-to-end defer behavior ───────────────────────────────────────
def _hass_with_lock():
    h = FakeHass()
    h.states.set("lock.front_door", "locked", friendly_name="Front Door")
    return h


async def test_unlock_defers_to_agent_when_confirmation_enabled(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch, protected=True)
    h = _hass_with_lock()
    result = await le.try_local(h, "unlock the front door", "sir")
    assert result is None                          # deferred to the agent
    assert h.service_calls == []                   # nothing actuated in the fast-path


async def test_unlock_actuates_fast_when_confirmation_disabled(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch, protected=False)
    h = _hass_with_lock()
    result = await le.try_local(h, "unlock the front door", "sir")
    assert result is not None and result.handled
    assert ("lock", "unlock", {"entity_id": "lock.front_door"}) in h.service_calls


async def test_bulk_unlock_defers_when_confirmation_enabled(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch, protected=True)
    h = _hass_with_lock()
    result = await le.try_local(h, "unlock all doors", "sir")
    assert result is None
    assert h.service_calls == []


async def test_turn_off_light_is_never_gated(load, monkeypatch):
    le = load("local_engine")
    _install_vc(monkeypatch, protected=True)       # confirmation on, but lights aren't protected
    h = FakeHass()
    h.states.set("light.kitchen", "on", friendly_name="Kitchen")
    result = await le.try_local(h, "turn off the kitchen light", "sir")
    assert result is not None and result.handled
    assert any(c[:2] == ("light", "turn_off") for c in h.service_calls)
