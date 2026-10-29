"""MCU Phase H — the cross-path thrash reactor: the event bus as a CONSUMED
nervous system.

The reactor subscribes to the actuation event stream (every path) and feeds a
cross-path LoopDetector; the discretionary-autonomous path consults it
(stream_thrash_check) and backs off per target on confirmed cross-path thrash —
an oscillation no single path's inline G2 gate can see. Bounded to the autonomous
population, FAILS OPEN, reversible via STREAM_THRASH_ENFORCE.
"""
from types import SimpleNamespace

import pytest

from fakes import FakeHass


@pytest.fixture
def act(load):
    mod = load("actuation")
    mod._stream_detector = None
    mod._autonomous_loop = None
    mod._loop_detector = None
    mod._agency_budget = None
    mod._autonomous_budget = None
    return mod


def _ev(cap, subject, eid="e"):
    return SimpleNamespace(type="control.actuation", data={"capability": cap},
                           subject=subject, correlation_id=None, id=eid)


# ── the reactor records the stream ────────────────────────────────────────────

def test_react_ignores_non_actuation_events(act):
    act._stream_react(SimpleNamespace(type="situation.transition", data={}, subject="x"))
    assert act._stream_detector is None          # nothing recorded


def test_react_records_actuation_events(act):
    act._stream_react(_ev("light.turn_on", "light.a", "e1"))
    assert act._stream_detector is not None       # a detector was created + fed


def test_stream_thrash_check_fails_open_without_detector(act):
    ok, reason = act.stream_thrash_check("light.turn_on:light.a")
    assert ok is True and reason is None          # no detector → fail open


def test_stream_thrash_check_flags_a_thrashing_key(act):
    # Feed the SAME key past the repetition threshold (the reactor records from
    # the stream); the consult then reports it thrashing — without recording.
    for i in range(6):
        act._stream_react(_ev("light.turn_on", "light.osc", f"e{i}"))
    ok, reason = act.stream_thrash_check("light.turn_on:light.osc")
    assert ok is False and "thrash" in reason


def test_stream_thrash_check_flag_off_is_fail_open(act):
    for i in range(6):
        act._stream_react(_ev("light.turn_on", "light.osc", f"e{i}"))
    ok, _r = act.stream_thrash_check("light.turn_on:light.osc", enforce=False)
    assert ok is True                             # kill-switch reverts to no-op


def test_register_stream_reactor_subscribes_to_actuation(act, load):
    bus = load("kernel.event_bus").JarvisEventBus()
    unsub = act.register_stream_reactor(bus)
    assert callable(unsub)
    # a published actuation event now reaches the reactor
    from_actuation = load("kernel.event").from_actuation
    bus.publish(from_actuation("light.turn_on", target="light.b"))
    assert act._stream_detector is not None


def test_peek_does_not_record(load):
    # kernel LoopDetector.peek is non-mutating: peeking never advances the count.
    J = load("kernel.loop_detect")
    d = J.LoopDetector()
    assert d.peek("k", now=1.0) is False
    # peeking many times must not trip the repetition threshold
    for _ in range(10):
        d.peek("k", now=1.0)
    assert d.peek("k", now=1.0) is False


# ── the autonomous path backs off on cross-path thrash ────────────────────────

@pytest.fixture
def cog(load, act):
    load("authority_bridge")
    return load("cognitive_core")


async def test_autonomous_backs_off_on_cross_path_thrash(cog, act):
    # Another path (the bus) has thrashed light.sx; the proactive path's own
    # inline G2 detector is clean, so ONLY the cross-path stream gate can catch
    # it. The action is suppressed (no service call, no success).
    for i in range(6):
        act._stream_react(_ev("light.turn_on", "light.sx", f"e{i}"))
    hass = FakeHass()
    hass.states.set("light.sx", "off")
    ok = await cog._execute_action_data(
        hass, {"domain": "light", "service": "turn_on", "entity_ids": ["light.sx"]})
    assert ok is False
    assert hass.service_calls == []


async def test_autonomous_proceeds_when_stream_is_quiet(cog, act):
    # No cross-path thrash → the action runs normally.
    hass = FakeHass()
    hass.states.set("light.calm", "off")
    ok = await cog._execute_action_data(
        hass, {"domain": "light", "service": "turn_on", "entity_ids": ["light.calm"]})
    assert ok is True
    assert hass.service_calls == [("light", "turn_on", {"entity_id": "light.calm"})]
