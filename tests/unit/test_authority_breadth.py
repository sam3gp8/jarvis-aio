"""Authority-breadth: run_scene_or_script and set_mode consult the authority
contract (MCU #4b parity, log-only).

These two were the consequential paths whose authority cell was an un-wired gap:
a scene/script or a mode change can apply anything a confirm-gate would guard,
yet neither recorded the kernel authority engine's verdict. Now each records
parity (authority_bridge.record_control_parity) at its activation point — the
same log-only treatment bulk_control already has. These tests pin that the
record fires with the right capability/target, and that it is strictly log-only:
it never gates the action, and a fault in it never disrupts the action.
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
async def test_scene_script_records_authority_parity(
        agent, load, fake_hass, monkeypatch, entity_id, domain, service):
    calls = _capture_parity(load, monkeypatch)
    actuation = load("actuation")

    seen = {}

    async def _exec(hass, **kw):
        seen["ran"] = True
        return True, ""
    monkeypatch.setattr(actuation, "execute_actuator", _exec)

    import json
    res = json.loads(await agent._exec_run_scene_script(
        fake_hass, {"entity_id": entity_id}))

    # Parity recorded for the right capability + target.
    assert len(calls) == 1
    assert calls[0]["domain"] == domain
    assert calls[0]["service"] == service
    assert calls[0]["allowed"] is True
    assert calls[0]["scope"] == entity_id
    # LOG-ONLY: the engine "would confirm", yet the activation still ran and
    # reported success — the parity record never gates.
    assert seen.get("ran") is True
    assert res.get("success") is True


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

async def test_set_mode_records_authority_parity(agent, load, fake_hass, monkeypatch):
    calls = _capture_parity(load, monkeypatch)
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
    assert len(calls) == 1
    assert calls[0]["domain"] == "jarvis"
    assert calls[0]["service"] == "set_mode"
    assert calls[0]["allowed"] is True
    assert calls[0]["scope"] == "night"


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
