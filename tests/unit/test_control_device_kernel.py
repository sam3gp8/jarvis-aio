"""MCU Phase A — control_device golden path: the control_device tool is being
migrated onto the kernel contract one stage at a time (WorldModel → … → Outcome
→ Event), authority staying log-only. These tests pin each stage's real wiring.

8.30.0 — world_model (parity): control_device reads its pre-action context
snapshot through the kernel WorldModel facade, not a bare states.get."""
import json
import logging
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


# ── 8.30.0: WorldModel is the pre-action context authority ───────────────────

async def test_previous_state_comes_from_worldmodel_snapshot(agent, fake_hass):
    """The result's previous_state is the snapshot state read via WorldModel."""
    fake_hass.states.set("light.den", "off")
    out = await agent._exec_control_device(
        fake_hass, {"entity_id": "light.den", "action": "turn_on"})
    await fake_hass.drain()
    res = json.loads(out)
    assert res["success"] is True
    assert res["previous_state"] == "off"


async def test_area_surfaced_from_worldmodel(agent, fake_hass):
    """WorldModel resolves the entity's area; control_device surfaces it."""
    fake_hass.states.set("light.den", "off", area="den")
    out = await agent._exec_control_device(
        fake_hass, {"entity_id": "light.den", "action": "turn_on"})
    await fake_hass.drain()
    res = json.loads(out)
    assert res["area"] == "den"


async def test_context_read_routes_through_worldmodel(agent, fake_hass, load, monkeypatch):
    """Prove the path uses WorldModel.device (not a raw states.get) for context:
    a patched facade returning a distinct snapshot shows up in the result."""
    wm_mod = load("kernel.world_model")
    sentinel = {"entity_id": "light.den", "domain": "light",
                "name": "Den", "state": "SENTINEL_PREV", "area": "SENTINEL_AREA",
                "attributes": {}}
    monkeypatch.setattr(wm_mod.WorldModel, "device", lambda self, eid: sentinel)
    fake_hass.states.set("light.den", "off")
    out = await agent._exec_control_device(
        fake_hass, {"entity_id": "light.den", "action": "turn_on"})
    await fake_hass.drain()
    res = json.loads(out)
    assert res["previous_state"] == "SENTINEL_PREV"
    assert res["area"] == "SENTINEL_AREA"


async def test_missing_entity_still_errors(agent, fake_hass):
    """No state and no snapshot → the not-found error is preserved."""
    out = await agent._exec_control_device(
        fake_hass, {"entity_id": "light.ghost", "action": "turn_on"})
    assert "not found" in out


# ── 8.31.0: the verify step produces the canonical ActuatorOutcome ───────────

@pytest.fixture
def no_sleep(agent, monkeypatch):
    async def _sleep(_secs):
        return None
    monkeypatch.setattr(agent, "_VERIFY_SLEEP", _sleep)


@pytest.fixture
def outcomes(load, monkeypatch):
    """Capture (request, status, detail) for every recorded ActuatorOutcome.

    Outcome recording now lives in the shared actuation envelope (B0)."""
    actuation = load("actuation")
    sink = []
    real = actuation.outcome

    def _spy(request, status, hass, entity_id, detail=""):
        sink.append({"request": request, "status": status, "detail": detail})
        return real(request, status, hass, entity_id, detail)
    monkeypatch.setattr(actuation, "outcome", _spy)
    return sink


async def test_outcome_verified_on_success(agent, fake_hass, no_sleep, outcomes):
    fake_hass.states.set("light.den", "off")
    async def flip(domain, service, data=None, blocking=False, **kw):
        fake_hass.service_calls.append((domain, service, dict(data or {})))
        fake_hass.states.set("light.den", "on")
    fake_hass.services.async_call = flip
    await agent._exec_control_device(
        fake_hass, {"entity_id": "light.den", "action": "turn_on"})
    await fake_hass.drain()
    assert [o["status"] for o in outcomes] == [agent._OUT_VERIFIED]
    # The outcome references the canonical ActuatorRequest built for the action.
    assert outcomes[0]["request"] is not None
    assert outcomes[0]["request"].expected_outcome == "on"


async def test_outcome_mismatch_when_world_never_reaches_expected(
        agent, fake_hass, no_sleep, outcomes):
    fake_hass.states.set("cover.garage_door", "open")   # never becomes 'closed'
    await agent._exec_control_device(
        fake_hass, {"entity_id": "cover.garage_door", "action": "close"})
    await fake_hass.drain()
    assert outcomes[-1]["status"] == agent._OUT_MISMATCH


async def test_no_outcome_for_non_deterministic_action(
        agent, fake_hass, no_sleep, outcomes):
    """set_brightness has no expected end-state, so no verify/outcome runs."""
    fake_hass.states.set("light.den", "on")
    await agent._exec_control_device(
        fake_hass, {"entity_id": "light.den", "action": "set_brightness", "value": 40})
    await fake_hass.drain()
    assert outcomes == []


# ── 8.32.0: the actuation is published as a canonical JarvisEvent ────────────

@pytest.fixture
def published(agent, load, monkeypatch):
    """Capture every JarvisEvent control_device publishes onto the bus."""
    ev_mod = load("events")
    sink = []
    monkeypatch.setattr(ev_mod, "publish", lambda hass, ev: sink.append(ev))
    return sink


async def test_actuation_publishes_canonical_event(agent, fake_hass, no_sleep, published):
    fake_hass.states.set("light.den", "off", area="den")
    await agent._exec_control_device(
        fake_hass, {"entity_id": "light.den", "action": "turn_on"})
    await fake_hass.drain()
    assert len(published) == 1
    ev = published[0]
    assert ev.type == "control.actuation"
    assert ev.source == "actuator"
    assert ev.subject == "light.den"
    assert ev.location == "den"
    assert ev.data["capability"] == "light.turn_on"
    assert ev.data["intent"] == "turn on"
    # The event links back to the correlated ActuatorRequest.
    assert ev.data["request_id"] is not None


async def test_parametric_action_publishes_event(agent, fake_hass, no_sleep, published):
    fake_hass.states.set("light.den", "on")
    await agent._exec_control_device(
        fake_hass, {"entity_id": "light.den", "action": "set_brightness", "value": 30})
    await fake_hass.drain()
    assert len(published) == 1
    assert published[0].data["capability"] == "light.turn_on"


async def test_unknown_action_publishes_nothing(agent, fake_hass, published):
    fake_hass.states.set("light.den", "on")
    out = await agent._exec_control_device(
        fake_hass, {"entity_id": "light.den", "action": "frobnicate"})
    assert "Unknown action" in out
    assert published == []


def test_from_actuation_builder(load):
    ev = load("kernel.event")
    e = ev.from_actuation("lock.lock", target="lock.front", intent="lock")
    assert e.type == ev.EVENT_ACTUATION == "control.actuation"
    assert e.subject == "lock.front"
    assert e.data["capability"] == "lock.lock"
    assert e.source == "actuator"


# ── 8.33.0: the actuation is expressed as a canonical one-step kernel Plan ───

def test_shadow_control_plan_shape(load):
    actuation = load("actuation")
    plan = actuation.plan_shadow("light.turn_on", "light.den", "turn_on",
                                 ("on",), correlation="cid1")
    assert plan is not None
    assert plan.goal == "turn_on light.den"
    assert plan.correlation_id == "cid1"
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.action == "light.turn_on"
    assert step.params == {"entity_id": "light.den"}
    assert step.preconditions == ("exists:light.den",)
    assert step.postconditions == ("state:on",)
    assert step.idempotency_key == "light.den:turn_on"


def test_shadow_control_plan_no_expected(load):
    """A non-deterministic action yields a plan with no postconditions."""
    actuation = load("actuation")
    plan = actuation.plan_shadow("light.turn_on", "light.den",
                                 "set_brightness", None)
    assert plan is not None and plan.steps[0].postconditions == ()


async def test_control_device_routes_execution_through_planner(agent, fake_hass, no_sleep, load, monkeypatch):
    """B4b: the actuation runs *through* the kernel planner (aexecute_plan), and
    the device service call still happens exactly once."""
    kplan = load("kernel.plan")
    calls = []
    real = kplan.aexecute_plan
    async def _spy(plan, **kw):
        calls.append(plan)
        return await real(plan, **kw)
    monkeypatch.setattr(kplan, "aexecute_plan", _spy)
    fake_hass.states.set("lock.front", "unlocked")
    out = await agent._exec_control_device(
        fake_hass, {"entity_id": "lock.front", "action": "lock"})
    await fake_hass.drain()
    assert '"success": true' in out.lower()
    # the actuation routed through the planner once (verify-after-act, which can
    # retry, is scheduled separately and is not this planner call)
    assert len(calls) == 1
    assert calls[0].steps[0].action == "lock.lock"
    assert ("lock", "lock") in [(c[0], c[1]) for c in fake_hass.service_calls]


async def test_parametric_action_routes_through_planner(agent, fake_hass, no_sleep, load, monkeypatch):
    kplan = load("kernel.plan")
    calls = []
    real = kplan.aexecute_plan
    async def _spy(plan, **kw):
        calls.append(plan)
        return await real(plan, **kw)
    monkeypatch.setattr(kplan, "aexecute_plan", _spy)
    fake_hass.states.set("light.den", "on")
    out = await agent._exec_control_device(
        fake_hass, {"entity_id": "light.den", "action": "set_brightness", "value": 40})
    await fake_hass.drain()
    assert '"success": true' in out.lower()
    assert len(calls) == 1 and calls[0].steps[0].action == "light.turn_on"


async def test_execution_failure_returns_error(agent, fake_hass, no_sleep):
    """If the service call raises, the planner reports failure and the path
    returns an error (not a bogus success)."""
    fake_hass.states.set("light.den", "off")
    async def boom(domain, service, data=None, blocking=False, **kw):
        raise RuntimeError("device offline")
    fake_hass.services.async_call = boom
    out = await agent._exec_control_device(
        fake_hass, {"entity_id": "light.den", "action": "turn_on"})
    assert '"error"' in out and "Failed" in out


def test_record_outcome_builds_actuator_outcome(fake_hass, load, caplog):
    """The envelope logs a real kernel ActuatorOutcome with observed state."""
    actuation = load("actuation")
    act = load("kernel.actuator")
    req = act.build_actuator_request("light.turn_on", target="light.den")
    fake_hass.states.set("light.den", "on")
    with caplog.at_level(logging.DEBUG):
        actuation.outcome(req, "verified", fake_hass, "light.den", "ok")
    msgs = [r.getMessage() for r in caplog.records]
    assert any("actuator(outcome):" in m and "verified" in m and "'observed': 'on'" in m
               for m in msgs)
