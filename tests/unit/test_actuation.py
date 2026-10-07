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


# ── #237: outcome() emits a kernel.outcome.Outcome in shadow ──────────────

def test_outcome_emits_shadow_kernel_outcome(actuation, load, fake_hass, monkeypatch):
    koutcome = load("kernel.outcome")
    captured = []
    real = koutcome.from_verification
    monkeypatch.setattr(koutcome, "from_verification",
                        lambda **kw: captured.append(real(**kw)) or captured[-1])
    req = actuation.request("light.turn_on", "light.den", action="turn_on",
                            expected=("on",))
    fake_hass.states.set("light.den", "on", area="den")
    actuation.outcome(req, "verified", fake_hass, "light.den", detail="ok")
    assert len(captured) == 1
    oc = captured[0]
    assert oc.success is True
    assert oc.capability == "light.turn_on"
    assert oc.observed_result == "on"
    assert oc.intended_result == "state:on"


def test_outcome_shadow_records_failure_verdict(actuation, load, fake_hass, monkeypatch):
    koutcome = load("kernel.outcome")
    captured = []
    real = koutcome.from_verification
    monkeypatch.setattr(koutcome, "from_verification",
                        lambda **kw: captured.append(real(**kw)) or captured[-1])
    req = actuation.request("lock.lock", "lock.front", action="lock",
                            expected=("locked",))
    fake_hass.states.set("lock.front", "unlocked", area="hall")
    actuation.outcome(req, "mismatch", fake_hass, "lock.front", detail="still unlocked")
    assert len(captured) == 1 and captured[0].success is False
    assert captured[0].learning_signal <= 0.0


def test_outcome_shadow_kill_switch_silences(actuation, load, fake_hass, monkeypatch):
    koutcome = load("kernel.outcome")
    called = {"n": 0}
    monkeypatch.setattr(koutcome, "from_verification",
                        lambda **kw: called.__setitem__("n", called["n"] + 1))
    monkeypatch.setattr(actuation, "OUTCOME_SHADOW", False)
    req = actuation.request("light.turn_on", "light.den", action="turn_on",
                            expected=("on",))
    fake_hass.states.set("light.den", "on", area="den")
    actuation.outcome(req, "verified", fake_hass, "light.den")
    assert called["n"] == 0        # kill-switch off → no shadow emission


def test_outcome_never_raises_on_bad_request(actuation, fake_hass):
    # Defensive: a None request must not raise from the shadow path.
    actuation.outcome(None, "verified", fake_hass, "light.ghost")
