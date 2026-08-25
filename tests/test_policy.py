"""Tests for the action-authorization policy gate (policy.py).

The load-bearing test is ``test_confirm_gate_fails_closed_when_confirm_raises``:
it guards the fix for the fail-open path where a confirmation-subsystem error
used to let a protected action (unlock/garage/disarm) execute anyway.
"""
from __future__ import annotations

import sys
import types


# ── fake voice_confirm sibling ───────────────────────────────────────────────
def _fake_vc(protected=True, confirm_result=True,
             confirm_raises=False, protected_raises=False):
    m = types.ModuleType("jc.voice_confirm")

    def action_is_protected(hass, domain, service, entity_id=""):
        if protected_raises:
            raise RuntimeError("protection check boom")
        return protected

    async def confirm(hass, question, entity_id=""):
        if confirm_raises:
            raise RuntimeError("confirm exploded")
        return confirm_result

    m.action_is_protected = action_is_protected
    m.confirm = confirm
    return m


def _install_vc(monkeypatch, fake):
    # Both are required: `from . import voice_confirm` uses the jc-package
    # attribute if one already exists (a prior test may have imported the real
    # module), else it falls back to sys.modules. monkeypatch restores both.
    monkeypatch.setitem(sys.modules, "jc.voice_confirm", fake)
    monkeypatch.setattr(sys.modules["jc"], "voice_confirm", fake, raising=False)


# ── classify (pure) ──────────────────────────────────────────────────────────
def test_classify_risk_levels(load):
    policy = load("policy")
    assert policy.classify("lock", "unlock")[0] == "high"
    assert policy.classify("alarm_control_panel", "alarm_disarm")[0] == "critical"
    assert policy.classify("cover", "open_cover")[0] == "medium"
    assert policy.classify("light", "turn_on")[0] == "low"
    assert policy.classify("media_player", "media_play")[0] == "low"
    assert policy.classify("climate", "set_temperature")[0] == "low"


def test_classify_security_switch(load):
    policy = load("policy")
    assert policy.classify("switch", "turn_off", "switch.front_door_lock")[0] == "high"
    assert policy.classify("switch", "turn_off", "switch.alarm_siren")[0] == "high"
    assert policy.classify("switch", "turn_off", "switch.desk_lamp")[0] == "low"


# ── confirm_gate ─────────────────────────────────────────────────────────────
async def test_confirm_gate_fails_closed_when_confirm_raises(load, monkeypatch):
    """THE regression guard: confirm() erroring on a protected action denies."""
    policy = load("policy")
    _install_vc(monkeypatch, _fake_vc(protected=True, confirm_raises=True))
    ok, note = await policy.confirm_gate(None, "lock", "unlock", "lock.front")
    assert ok is False
    assert "denied" in note.lower() or "unavailable" in note.lower()


async def test_confirm_gate_allows_when_confirmed(load, monkeypatch):
    policy = load("policy")
    _install_vc(monkeypatch, _fake_vc(protected=True, confirm_result=True))
    ok, note = await policy.confirm_gate(None, "lock", "unlock", "lock.front")
    assert ok is True
    assert note == ""


async def test_confirm_gate_denies_when_declined(load, monkeypatch):
    policy = load("policy")
    _install_vc(monkeypatch, _fake_vc(protected=True, confirm_result=False))
    ok, note = await policy.confirm_gate(None, "lock", "unlock", "lock.front")
    assert ok is False
    assert "not yet confirmed" in note


async def test_confirm_gate_no_friction_when_not_protected(load, monkeypatch):
    policy = load("policy")
    _install_vc(monkeypatch, _fake_vc(protected=False))
    ok, note = await policy.confirm_gate(None, "light", "turn_on", "light.kitchen")
    assert ok is True
    assert note == ""


async def test_confirm_gate_fails_closed_when_protection_check_raises(load, monkeypatch):
    policy = load("policy")
    _install_vc(monkeypatch, _fake_vc(protected_raises=True))
    # high-risk action -> denied when the checker errors
    ok, _ = await policy.confirm_gate(None, "lock", "unlock", "lock.front")
    assert ok is False
    # low-risk convenience still flows even if the checker errors
    ok2, _ = await policy.confirm_gate(None, "light", "turn_on", "light.kitchen")
    assert ok2 is True


# ── requires_confirmation (sync, used by bulk_control) ────────────────────────
def test_requires_confirmation_passthrough(load, monkeypatch):
    policy = load("policy")
    _install_vc(monkeypatch, _fake_vc(protected=True))
    assert policy.requires_confirmation(None, "lock", "unlock", "lock.front") is True
    _install_vc(monkeypatch, _fake_vc(protected=False))
    assert policy.requires_confirmation(None, "lock", "unlock", "lock.front") is False


def test_requires_confirmation_fails_closed_on_error(load, monkeypatch):
    policy = load("policy")
    _install_vc(monkeypatch, _fake_vc(protected_raises=True))
    # non-low risk -> require confirmation when the check errors
    assert policy.requires_confirmation(None, "lock", "unlock", "lock.front") is True
    # low risk -> allowed
    assert policy.requires_confirmation(None, "light", "turn_on", "light.kitchen") is False
