"""MCU Phase H — the situation reactor: the situation-transition stream as a
CONSUMED nervous system.

A bus subscriber (``actuation._situation_react``, registered at setup via
``register_situation_reactor``) tracks which safety-critical situations
(intrusion / hazard) are currently ELEVATED. The discretionary-autonomous path
consults it (``safety_situation_active``) and pauses routine autonomy while a
security / safety event is being handled — a butler stops the routine fussing
when there's a concern. Bounded to the autonomous population, FAILS OPEN,
reversible via ``SITUATION_AUTONOMY_SUPPRESS_ENFORCE``. A delivery is NOT a safety
situation: its transitions reach the reactor but never pause autonomy.
"""
from types import SimpleNamespace

import pytest

from fakes import FakeHass


@pytest.fixture
def act(load):
    mod = load("actuation")
    mod._active_safety_situations = {}
    mod._stream_detector = None
    mod._autonomous_loop = None
    mod._loop_detector = None
    mod._agency_budget = None
    mod._autonomous_budget = None
    return mod


def _ev(kind, state, sid="s1"):
    return SimpleNamespace(
        type="situation.transition",
        data={"kind": kind, "state": state, "situation_id": sid},
        subject=sid, id="e1", correlation_id=None)


# ── the reactor tracks elevated safety situations ────────────────────────────────

def test_react_ignores_non_situation_events(act):
    act._situation_react(SimpleNamespace(type="control.actuation", data={}, subject="x"))
    assert act.safety_situation_active() == (False, ())


def test_react_tracks_elevated_intrusion(act):
    act._situation_react(_ev("intrusion", "investigating"))
    active, kinds = act.safety_situation_active()
    assert active is True and "intrusion" in kinds


def test_react_tracks_elevated_hazard(act):
    act._situation_react(_ev("hazard", "confirmed"))
    active, kinds = act.safety_situation_active()
    assert active is True and "hazard" in kinds


def test_react_ignores_delivery(act):
    # A package is not a safety situation — recorded nowhere, never pauses autonomy.
    act._situation_react(_ev("delivery", "investigating"))
    assert act.safety_situation_active() == (False, ())


def test_benign_does_not_activate(act):
    # A false alarm (BENIGN) must not pause autonomy.
    act._situation_react(_ev("intrusion", "benign"))
    assert act.safety_situation_active() == (False, ())


def test_resolution_clears_the_situation(act):
    act._situation_react(_ev("intrusion", "confirmed", sid="s1"))
    assert act.safety_situation_active()[0] is True
    act._situation_react(_ev("intrusion", "resolved", sid="s1"))
    assert act.safety_situation_active() == (False, ())


def test_overlapping_episodes_are_independent(act):
    act._situation_react(_ev("intrusion", "investigating", sid="a"))
    act._situation_react(_ev("hazard", "confirmed", sid="b"))
    act._situation_react(_ev("intrusion", "resolved", sid="a"))   # a stands down
    active, kinds = act.safety_situation_active()
    assert active is True and kinds == ("hazard",)                # b still elevated


def test_kill_switch_off_never_suppresses(act):
    act._situation_react(_ev("intrusion", "confirmed"))
    assert act.safety_situation_active(enforce=False) == (False, ())


def test_default_kill_switch_is_enforce(act):
    assert act.SITUATION_AUTONOMY_SUPPRESS_ENFORCE is True


def test_register_subscribes_to_situation_events(act, load):
    bus = load("kernel.event_bus").JarvisEventBus()
    unsub = act.register_situation_reactor(bus)   # no hass → skip reconcile
    assert callable(unsub)
    from_situation = load("kernel.event").from_situation
    bus.publish(from_situation("intrusion", state="investigating", situation_id="z"))
    assert act.safety_situation_active()[0] is True


# ── the autonomous path pauses during a safety situation ─────────────────────────

@pytest.fixture
def cog(load, act):
    load("authority_bridge")
    return load("cognitive_core")


async def test_autonomous_pauses_during_safety_situation(cog, act):
    # An intrusion is being handled → discretionary autonomy is suppressed en
    # masse (no service call, no success claim).
    act._active_safety_situations["s1"] = "intrusion"
    hass = FakeHass()
    hass.states.set("light.x", "off")
    ok = await cog._execute_action_data(
        hass, {"domain": "light", "service": "turn_on", "entity_ids": ["light.x"]})
    assert ok is False
    assert hass.service_calls == []


async def test_autonomous_proceeds_when_no_safety_situation(cog, act):
    # No safety situation active → the action runs normally.
    hass = FakeHass()
    hass.states.set("light.calm", "off")
    ok = await cog._execute_action_data(
        hass, {"domain": "light", "service": "turn_on", "entity_ids": ["light.calm"]})
    assert ok is True
    assert hass.service_calls == [("light", "turn_on", {"entity_id": "light.calm"})]
