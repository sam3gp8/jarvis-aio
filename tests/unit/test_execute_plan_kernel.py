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


@pytest.fixture
def authority_calls(load, monkeypatch):
    """Spy on the kernel authority engine's per-step verdict (MCU #4b)."""
    ab = load("authority_bridge")
    calls = []

    def _spy(hass, d, s, *, legacy_ok=True, **k):
        calls.append((d, s, legacy_ok))
        return legacy_ok, None

    monkeypatch.setattr(ab, "enforced_decision", _spy)
    return calls


def _steps(*specs):
    return [{"domain": d, "service": s, "entity_id": e} for d, s, e in specs]


async def test_each_step_runs_through_kernel_authority(agent, fake_hass, published, authority_calls):
    # #4b: execute_plan's authority contract — every step runs through the kernel
    # authority engine (authority_bridge.enforced_decision), not just the legacy gate.
    fake_hass.states.set("light.den", "off")
    fake_hass.states.set("switch.fan", "off")
    await agent._exec_execute_plan(fake_hass, {"steps": _steps(
        ("light", "turn_on", "light.den"),
        ("switch", "turn_on", "switch.fan"))})
    assert authority_calls == [("light", "turn_on", True), ("switch", "turn_on", True)]


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


async def test_each_step_reads_world_model_and_area_tags_event(
        agent, fake_hass, load, monkeypatch, published):
    # MCU world_model parity: each step reads its target's pre-action context
    # through the WorldModel facade (actuation.context) — the canonical context
    # authority — and the emitted event is area-tagged, the same read
    # control_device/bulk do, rather than acting on raw state alone.
    actuation = load("actuation")
    seen = []
    real_ctx = actuation.context

    def _spy(hass, eid):
        seen.append(eid)
        c = dict(real_ctx(hass, eid))
        c["area"] = f"area-of-{eid.split('.')[-1]}"   # resolved area for this target
        return c

    monkeypatch.setattr(actuation, "context", _spy)
    fake_hass.states.set("light.den", "off")
    fake_hass.states.set("switch.fan", "off")
    await agent._exec_execute_plan(fake_hass, {"steps": _steps(
        ("light", "turn_on", "light.den"),
        ("switch", "turn_on", "switch.fan"))})
    # context read exactly once per step, for that step's own target
    assert seen == ["light.den", "switch.fan"]
    # each successful step's actuation event carries its resolved area (location)
    locs = {e.data["capability"]: e.location for e in published}
    assert locs == {"light.turn_on": "area-of-den", "switch.turn_on": "area-of-fan"}


async def test_world_model_read_is_best_effort_never_blocks_step(
        agent, fake_hass, load, monkeypatch, published):
    # The context read is best-effort: even if the facade raised, the step must
    # still run (behaviour preserved). actuation.context itself never raises, but
    # a defensive path shouldn't let world_model wiring gate execution.
    actuation = load("actuation")
    monkeypatch.setattr(actuation, "context",
                        lambda hass, eid: {"area": None, "exists": True})
    fake_hass.states.set("light.den", "off")
    out = await agent._exec_execute_plan(fake_hass, {"steps": _steps(
        ("light", "turn_on", "light.den"))})
    res = json.loads(out)
    assert res["succeeded"] == 1 and res["failed"] == 0
    assert ("light", "turn_on") in [(c[0], c[1]) for c in fake_hass.service_calls]
    assert len(published) == 1 and published[0].location is None


async def test_empty_plan(agent, fake_hass):
    out = await agent._exec_execute_plan(fake_hass, {"steps": []})
    assert "no steps provided" in out
