"""MCU Phase B / B0 — the shared actuation envelope (actuation.py).

The golden-path wiring extracted from control_device so every actuator path
routes device changes through one kernel contract. These pin the envelope
directly; control_device's own tests exercise it end-to-end."""
import pytest


@pytest.fixture
def actuation(load):
    return load("actuation")


def test_context_reads_through_worldmodel(actuation, fake_hass):
    fake_hass.states.set("light.den", "off", area="den")
    ctx = actuation.context(fake_hass, "light.den")
    assert ctx["exists"] is True
    assert ctx["prev_state"] == "off"
    assert ctx["area"] == "den"
    assert ctx["raw"] is not None


def test_context_missing_entity(actuation, fake_hass):
    ctx = actuation.context(fake_hass, "light.ghost")
    assert ctx["exists"] is False
    assert ctx["prev_state"] == "unknown"
    assert ctx["area"] is None


def test_request_carries_expected_and_idempotency(actuation):
    req = actuation.request("light.turn_on", "light.den",
                            params={"entity_id": "light.den"}, action="turn_on",
                            intent="turn on", expected=("on",))
    assert req is not None
    assert req.capability == "light.turn_on"
    assert req.target == "light.den"
    assert req.expected_outcome == "on"
    assert req.idempotency_key == "light.den:turn_on"
    assert req.intent == "turn on"


def test_request_without_expected(actuation):
    req = actuation.request("media_player.volume_set", "media_player.den",
                            action="volume_set")
    assert req is not None and req.expected_outcome is None


def test_plan_shadow_one_step(actuation):
    plan = actuation.plan_shadow("lock.lock", "lock.front", "lock", ("locked",))
    assert plan is not None and len(plan.steps) == 1
    assert plan.steps[0].postconditions == ("state:locked",)


def test_emit_event_is_best_effort_without_bus(actuation, fake_hass):
    # No event bus in hass.data → publish is a silent no-op, never raises.
    actuation.emit_event(fake_hass, "light.turn_on", "light.den", action="turn_on")


def test_emit_event_publishes_when_bus_present(actuation, load, fake_hass, monkeypatch):
    events = load("events")
    sink = []
    monkeypatch.setattr(events, "publish", lambda hass, ev: sink.append(ev))
    req = actuation.request("light.turn_on", "light.den", action="turn_on")
    actuation.emit_event(fake_hass, "light.turn_on", "light.den",
                         action="turn_on", area="den", request=req)
    assert len(sink) == 1
    assert sink[0].type == "control.actuation"
    assert sink[0].data["request_id"] == req.id
    assert sink[0].location == "den"
