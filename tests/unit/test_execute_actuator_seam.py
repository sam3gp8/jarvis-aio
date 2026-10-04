"""MCU Phase H (H1) — the universal actuator seam.

``actuation.execute_actuator`` is the single place every consequential actuation
converges on: Execution (through the kernel planner) → Event → (scheduled)
Verification/Outcome. control_device routes through it; bulk_control, scenes,
goals, FRIDAY, … migrate onto it next. These pin the seam's own contract.
"""
import pytest


@pytest.fixture
def actuation(load):
    return load("actuation")


def _req(load, cap="light.turn_on", target="light.den", **kw):
    act = load("kernel.actuator")
    return act.build_actuator_request(cap, target=target, **kw)


async def test_seam_executes_through_planner_once(actuation, fake_hass, load, monkeypatch):
    kplan = load("kernel.plan")
    calls = []
    real = kplan.aexecute_plan

    async def _spy(plan, **kw):
        calls.append(plan)
        return await real(plan, **kw)
    monkeypatch.setattr(kplan, "aexecute_plan", _spy)

    fake_hass.states.set("light.den", "off")
    ok, detail = await actuation.execute_actuator(
        fake_hass, capability="light.turn_on", entity_id="light.den",
        domain="light", service="turn_on", data={"entity_id": "light.den"},
        action="turn_on", areq=_req(load))
    assert ok is True and detail == ""
    assert len(calls) == 1
    assert calls[0].steps[0].action == "light.turn_on"
    assert ("light", "turn_on") in [(c[0], c[1]) for c in fake_hass.service_calls]


async def test_seam_publishes_event_on_success(actuation, fake_hass, load, monkeypatch):
    # The seam calls the shared envelope's emit_event on a successful execute
    # (emit_event's own JarvisEvent wiring is covered by the control_device tests).
    emitted = []
    monkeypatch.setattr(
        actuation, "emit_event",
        lambda hass, cap, eid, **kw: emitted.append((cap, eid, kw)))
    fake_hass.states.set("light.den", "off", area="den")
    ok, _ = await actuation.execute_actuator(
        fake_hass, capability="light.turn_on", entity_id="light.den",
        domain="light", service="turn_on", data={"entity_id": "light.den"},
        action="turn_on", areq=_req(load), area="den")
    assert ok is True
    assert len(emitted) == 1
    cap, eid, kw = emitted[0]
    assert cap == "light.turn_on" and eid == "light.den"
    assert kw.get("area") == "den" and kw.get("action") == "turn_on"


async def test_seam_schedules_verify_on_success(actuation, fake_hass, load):
    fake_hass.states.set("lock.front", "unlocked")
    ran = {"verify": False}

    async def _verify():
        ran["verify"] = True
    ok, _ = await actuation.execute_actuator(
        fake_hass, capability="lock.lock", entity_id="lock.front",
        domain="lock", service="lock", data={"entity_id": "lock.front"},
        action="lock", areq=_req(load, "lock.lock", "lock.front"),
        verify=lambda: _verify())
    await fake_hass.drain()
    assert ok is True
    assert ran["verify"] is True


async def test_seam_failure_returns_error_no_event_no_verify(actuation, fake_hass, load, monkeypatch):
    ev_mod = load("events")
    published = []
    monkeypatch.setattr(ev_mod, "publish", lambda hass, ev: published.append(ev))
    fake_hass.states.set("light.den", "off")

    async def boom(domain, service, data=None, blocking=False, **kw):
        raise RuntimeError("device offline")
    fake_hass.services.async_call = boom

    ran = {"verify": False}

    async def _verify():
        ran["verify"] = True

    ok, detail = await actuation.execute_actuator(
        fake_hass, capability="light.turn_on", entity_id="light.den",
        domain="light", service="turn_on", data={"entity_id": "light.den"},
        action="turn_on", areq=_req(load), verify=lambda: _verify())
    await fake_hass.drain()
    assert ok is False
    assert detail  # carries the failure reason
    assert published == []       # no event on failure
    assert ran["verify"] is False  # no verify scheduled on failure


async def test_seam_honors_blocking_flag(actuation, fake_hass, load):
    # bulk/fan-out callers pass blocking=False for fire-and-forget; the seam
    # threads it to the HA service call (default True for control_device verify).
    seen = {}

    async def _call(domain, service, data=None, blocking=True, **kw):
        seen["blocking"] = blocking
        fake_hass.service_calls.append((domain, service, dict(data or {})))
    fake_hass.services.async_call = _call
    fake_hass.states.set("light.a", "off")
    ok, _ = await actuation.execute_actuator(
        fake_hass, capability="light.turn_on", entity_id="light.a",
        domain="light", service="turn_on", data={"entity_id": "light.a"},
        action="turn_on", areq=_req(load, target="light.a"), blocking=False)
    assert ok is True
    assert seen["blocking"] is False


async def test_seam_no_verify_when_factory_none(actuation, fake_hass, load):
    # A non-deterministic action passes verify=None → nothing scheduled, still ok.
    fake_hass.states.set("light.den", "on")
    ok, _ = await actuation.execute_actuator(
        fake_hass, capability="light.turn_on", entity_id="light.den",
        domain="light", service="turn_on",
        data={"entity_id": "light.den", "brightness_pct": 40},
        action="set_brightness", areq=_req(load), verify=None)
    await fake_hass.drain()
    assert ok is True
