"""Authority-breadth: run_scene_or_script and set_mode are ENFORCED by the
kernel authority engine (MCU Phase G4b, general enforce).

These two were the consequential paths whose authority cell was an un-wired gap:
a scene/script or a mode change can apply anything a confirm-gate would guard.
Each now routes through authority_bridge.enforced_decision at its activation
point (which records parity too) — the same max-restriction belt control_device
has, now general. These tests pin that the engine's verdict GATES the action
(a would-confirm holds it) while a fault in the engine fails safe (the action
still runs), and that an ALLOW verdict lets it proceed.
"""
from types import SimpleNamespace

import pytest


@pytest.fixture
def agent(load):
    load("actuation")
    load("authority_bridge")
    return load("agent")


def _capture_parity(load, monkeypatch):
    calls = []
    ab = load("authority_bridge")

    def _rec(hass, domain, service, *, allowed, scope=None, intent=None, **kw):
        calls.append({"domain": domain, "service": service, "allowed": allowed,
                      "scope": scope, "intent": intent})
        return SimpleNamespace(decision="confirm", reason="test")  # would-hold
    monkeypatch.setattr(ab, "record_control_parity", _rec)
    return calls


# ── run_scene_or_script ──────────────────────────────────────────────────────

@pytest.mark.parametrize("entity_id, domain, service", [
    ("scene.movie_night", "scene", "turn_on"),
    ("script.bedtime", "script", "turn_on"),
    ("automation.porch", "automation", "trigger"),
])
async def test_scene_script_authority_enforced_holds_on_confirm(
        agent, load, fake_hass, monkeypatch, entity_id, domain, service):
    calls = _capture_parity(load, monkeypatch)   # engine returns "confirm"
    actuation = load("actuation")

    seen = {}

    async def _exec(hass, **kw):
        seen["ran"] = True
        return True, ""
    monkeypatch.setattr(actuation, "execute_actuator", _exec)

    import json
    res = json.loads(await agent._exec_run_scene_script(
        fake_hass, {"entity_id": entity_id}))

    # Enforce recorded (via enforced_decision) for the right capability + target.
    assert len(calls) == 1
    assert calls[0]["domain"] == domain
    assert calls[0]["service"] == service
    assert calls[0]["scope"] == entity_id
    # ENFORCED: the engine would confirm, so the activation is HELD — it never
    # ran and the result flags needs_confirmation.
    assert seen.get("ran") is None
    assert res.get("needs_confirmation") is True
    assert res.get("success") is not True


@pytest.mark.parametrize("entity_id, domain, service", [
    ("scene.movie_night", "scene", "turn_on"),
    ("script.bedtime", "script", "turn_on"),
])
async def test_scene_script_runs_when_engine_allows(
        agent, load, fake_hass, monkeypatch, entity_id, domain, service):
    ab = load("authority_bridge")
    monkeypatch.setattr(ab, "record_control_parity",
                        lambda *a, **k: SimpleNamespace(decision="allow", reason="ok"))
    actuation = load("actuation")
    seen = {}

    async def _exec(hass, **kw):
        seen["ran"] = True
        return True, ""
    monkeypatch.setattr(actuation, "execute_actuator", _exec)

    import json
    res = json.loads(await agent._exec_run_scene_script(
        fake_hass, {"entity_id": entity_id}))
    assert seen.get("ran") is True and res.get("success") is True


async def test_scene_parity_fault_never_disrupts_activation(
        agent, load, fake_hass, monkeypatch):
    ab = load("authority_bridge")
    actuation = load("actuation")

    def _boom(*a, **k):
        raise RuntimeError("parity backend down")
    monkeypatch.setattr(ab, "record_control_parity", _boom)

    seen = {}

    async def _exec(hass, **kw):
        seen["ran"] = True
        return True, ""
    monkeypatch.setattr(actuation, "execute_actuator", _exec)

    import json
    res = json.loads(await agent._exec_run_scene_script(
        fake_hass, {"entity_id": "scene.movie_night"}))
    # A fault in the log-only parity path is swallowed; the scene still activates.
    assert seen.get("ran") is True
    assert res.get("success") is True


# ── set_mode ─────────────────────────────────────────────────────────────────

async def test_set_mode_authority_enforced_holds_on_confirm(agent, load, fake_hass, monkeypatch):
    calls = _capture_parity(load, monkeypatch)   # engine "confirm"
    modes = load("modes")
    applied = {}
    monkeypatch.setattr(modes, "set_mode",
                        lambda mode, reason: applied.setdefault("set", mode) and None
                        or {"ok": True, "mode": mode})

    import json
    res = json.loads(await agent._exec_set_mode(fake_hass, {"mode": "night"}))

    # ENFORCED: the engine would confirm, so the directive is held BEFORE it is
    # applied — modes.set_mode was never called.
    assert len(calls) == 1
    assert calls[0]["domain"] == "jarvis"
    assert calls[0]["service"] == "set_mode"
    assert calls[0]["scope"] == "night"
    assert res.get("needs_confirmation") is True
    assert res.get("ok") is not True
    assert "set" not in applied


async def test_set_mode_applies_when_engine_allows(agent, load, fake_hass, monkeypatch):
    ab = load("authority_bridge")
    monkeypatch.setattr(ab, "record_control_parity",
                        lambda *a, **k: SimpleNamespace(decision="allow", reason="ok"))
    modes = load("modes")
    mode_scene = load("mode_scene")
    monkeypatch.setattr(modes, "set_mode", lambda mode, reason: {"ok": True, "mode": mode})
    monkeypatch.setattr(modes, "mode_info", lambda: {"description": "Night mode"})

    async def _apply(hass, mode):
        return None
    monkeypatch.setattr(mode_scene, "apply_mode_entry", _apply)

    import json
    res = json.loads(await agent._exec_set_mode(fake_hass, {"mode": "night"}))
    assert res.get("ok") is True and res.get("mode") == "night"


async def test_set_mode_parity_fault_never_disrupts_switch(agent, load, fake_hass, monkeypatch):
    ab = load("authority_bridge")
    modes = load("modes")
    mode_scene = load("mode_scene")

    def _boom(*a, **k):
        raise RuntimeError("parity backend down")
    monkeypatch.setattr(ab, "record_control_parity", _boom)
    monkeypatch.setattr(modes, "set_mode", lambda mode, reason: {"ok": True, "mode": mode})
    monkeypatch.setattr(modes, "mode_info", lambda: {})

    async def _apply(hass, mode):
        return None
    monkeypatch.setattr(mode_scene, "apply_mode_entry", _apply)

    import json
    res = json.loads(await agent._exec_set_mode(fake_hass, {"mode": "away"}))
    # The mode switch succeeds despite the parity fault (log-only, swallowed).
    assert res.get("ok") is True and res.get("mode") == "away"
