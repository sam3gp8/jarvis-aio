"""MCU Phase B (B4) — execute_plan routes each step through the kernel planner.

Behaviour is preserved: steps run independently, the loop continues on failure
and collects every per-step result; each successful step publishes a canonical
actuation event. Each step is its own one-step kernel plan (aexecute_plan)."""
import json
import sys
import types

import pytest

if "homeassistant.helpers.llm" not in sys.modules:
    _llm = types.ModuleType("homeassistant.helpers.llm")
    _llm.async_get_api = lambda *a, **k: None
    sys.modules["homeassistant.helpers.llm"] = _llm


@pytest.fixture
def agent(load):
    return load("agent")


@pytest.fixture
def published(load, monkeypatch):
    events = load("events")
    sink = []
    monkeypatch.setattr(events, "publish", lambda hass, ev: sink.append(ev))
    return sink


def _steps(*specs):
    return [{"domain": d, "service": s, "entity_id": e} for d, s, e in specs]


async def test_multi_step_success_runs_all(agent, fake_hass, published):
    fake_hass.states.set("light.den", "off")
    fake_hass.states.set("switch.fan", "off")
    out = await agent._exec_execute_plan(fake_hass, {
        "goal": "evening", "steps": _steps(
            ("light", "turn_on", "light.den"),
            ("switch", "turn_on", "switch.fan"))})
    res = json.loads(out)
    assert res["succeeded"] == 2 and res["failed"] == 0
    assert all(r["ok"] for r in res["results"])
    # execution actually happened (routed through the planner's run_step)
    calls = [(c[0], c[1]) for c in fake_hass.service_calls]
    assert ("light", "turn_on") in calls and ("switch", "turn_on") in calls
    # one actuation event per successful step
    assert len(published) == 2
    assert {e.data["capability"] for e in published} == {"light.turn_on", "switch.turn_on"}


async def test_continue_on_failure(agent, fake_hass, published):
    # middle step's entity is missing → it fails, the others still run
    fake_hass.states.set("light.den", "off")
    fake_hass.states.set("light.hall", "off")
    out = await agent._exec_execute_plan(fake_hass, {"steps": _steps(
        ("light", "turn_on", "light.den"),
        ("light", "turn_on", "light.ghost"),   # missing
        ("light", "turn_on", "light.hall"))})
    res = json.loads(out)
    assert res["succeeded"] == 2 and res["failed"] == 1
    assert res["results"][1]["ok"] is False and "not found" in res["results"][1]["error"]
    assert res["results"][0]["ok"] and res["results"][2]["ok"]
    assert len(published) == 2   # only the two that ran


async def test_missing_fields_errors_without_running(agent, fake_hass, published):
    out = await agent._exec_execute_plan(fake_hass, {"steps": [
        {"domain": "light", "service": "", "entity_id": "light.den"}]})
    res = json.loads(out)
    assert res["failed"] == 1 and "missing" in res["results"][0]["error"]
    assert published == []


async def test_confirm_gate_blocks_a_step(agent, fake_hass, load, monkeypatch, published):
    policy = load("policy")
    async def gate(hass, dom, svc, eid, phrase):
        return (False, "confirmation required") if eid == "lock.front" else (True, None)
    monkeypatch.setattr(policy, "confirm_gate", gate)
    fake_hass.states.set("light.den", "off")
    fake_hass.states.set("lock.front", "unlocked")
    out = await agent._exec_execute_plan(fake_hass, {"steps": _steps(
        ("light", "turn_on", "light.den"),
        ("lock", "lock", "lock.front"))})
    res = json.loads(out)
    assert res["succeeded"] == 1 and res["failed"] == 1
    assert res["results"][1]["ok"] is False
    assert "confirmation" in res["results"][1]["error"]
    # the blocked step never executed
    assert ("lock", "lock") not in [(c[0], c[1]) for c in fake_hass.service_calls]


async def test_empty_plan(agent, fake_hass):
    out = await agent._exec_execute_plan(fake_hass, {"steps": []})
    assert "no steps provided" in out
