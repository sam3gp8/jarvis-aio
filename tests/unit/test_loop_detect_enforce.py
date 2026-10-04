"""MCU Phase G (G2): the kernel loop detector is ENFORCED on the discretionary
autonomous actuation path — the second staged enforce flip.

When JARVIS acts on a learned/trusted pattern of its own accord
(`cognitive_core._execute_action_data`) and the same action thrashes (re-fires
too many times in a tight window, or self-triggers through the event chain), the
actuation is actually SUPPRESSED, not merely logged. Same tight scope as the
budget flip (G1):

  * user-requested actuations (the agent tool path) never reach it, and
  * safety-critical responses (lockdown, intrusion securing) call hass.services
    directly and never route through it,

so neither a user command nor a safety action can ever be loop-suppressed. The
threshold is deliberately HIGH (5 identical firings within 60s), the gate fails
open, and a one-line kill-switch (`LOOP_DETECT_ENFORCE`) reverts it to shadow.
"""
import logging

import pytest

from fakes import FakeHass


@pytest.fixture
def act(load):
    mod = load("actuation")
    mod._autonomous_loop = None             # fresh detector per test
    mod._autonomous_budget = None
    mod._loop_detector = None
    mod._agency_budget = None
    return mod


# ── the enforce gate itself ─────────────────────────────────────────────────

def test_gate_allows_a_non_looping_action(act):
    allowed, reason = act.loop_detect_check("light.turn_on:light.a",
                                            enforce=True, now=1000.0)
    assert allowed is True
    assert reason == "none"


def test_gate_suppresses_a_thrashing_action_when_enforcing(act):
    # The high threshold is 5 firings of the identical key within 60s.
    key = "light.turn_on:light.a"
    for i in range(4):
        allowed, _ = act.loop_detect_check(key, enforce=True, now=1000.0 + i)
        assert allowed is True              # first four pass
    allowed, reason = act.loop_detect_check(key, enforce=True, now=1004.0)
    assert allowed is False                 # fifth trips the loop
    assert reason == "repetition"


def test_gate_holds_during_cooldown_after_a_loop(act):
    key = "light.turn_on:light.a"
    for i in range(5):
        act.loop_detect_check(key, enforce=True, now=1000.0 + i)   # trips on 5th
    # Still within the 60s cooldown → keep suppressing to break the cycle.
    allowed, reason = act.loop_detect_check(key, enforce=True, now=1010.0)
    assert allowed is False
    assert reason == "cooldown"


def test_gate_does_not_suppress_when_kill_switch_off(act, caplog):
    key = "light.turn_on:light.a"
    for i in range(4):
        act.loop_detect_check(key, enforce=False, now=2000.0 + i)
    with caplog.at_level(logging.WARNING,
                         logger="custom_components.jarvis.actuation"):
        allowed, reason = act.loop_detect_check(key, enforce=False, now=2004.0)
    assert allowed is True                  # shadow: logged, not suppressed
    assert reason == "repetition"
    assert any("NOT suppressed" in r.message for r in caplog.records)


def test_gate_fails_open_on_error(act, monkeypatch):
    class _Boom:
        def record(self, *a, **k):
            raise RuntimeError("boom")
    monkeypatch.setattr(act, "_autonomous_loop", _Boom())
    allowed, reason = act.loop_detect_check("x", enforce=True, now=1.0)
    assert allowed is True
    assert reason == "error"


def test_default_kill_switch_is_enforce(act):
    assert act.LOOP_DETECT_ENFORCE is True


def test_distinct_actions_do_not_trip_each_other(act):
    # Five DIFFERENT actions in the window must all pass — only repetition of the
    # SAME key is a loop.
    for i in range(5):
        allowed, _ = act.loop_detect_check(f"light.turn_on:light.{i}",
                                           enforce=True, now=3000.0)
        assert allowed is True


# ── the live autonomous path honors the gate ────────────────────────────────

@pytest.fixture
def cog(load, act):
    return load("cognitive_core")


async def test_autonomous_action_executes_when_not_looping(cog):
    hass = FakeHass()
    ok = await cog._execute_action_data(
        hass, {"domain": "light", "service": "turn_on",
               "entity_ids": ["light.lamp"]})
    assert ok is True
    assert hass.service_calls == [("light", "turn_on", {"entity_id": ["light.lamp"]})]


async def test_autonomous_action_suppressed_once_thrashing(cog, caplog):
    hass = FakeHass()
    action = {"domain": "light", "service": "turn_on", "entity_ids": ["light.lamp"]}
    # First four identical autonomous firings execute; the fifth is suppressed.
    for _ in range(4):
        assert await cog._execute_action_data(hass, action) is True
    with caplog.at_level(logging.WARNING,
                         logger="custom_components.jarvis.cognitive_core"):
        ok = await cog._execute_action_data(hass, action)
    assert ok is False
    assert len(hass.service_calls) == 4     # the 5th call was SUPPRESSED
    assert any("suppressed by loop detector" in r.message for r in caplog.records)


async def test_autonomous_action_not_suppressed_with_kill_switch_off(cog, act, monkeypatch):
    monkeypatch.setattr(act, "LOOP_DETECT_ENFORCE", False)
    hass = FakeHass()
    action = {"domain": "light", "service": "turn_on", "entity_ids": ["light.lamp"]}
    for _ in range(6):
        assert await cog._execute_action_data(hass, action) is True
    assert len(hass.service_calls) == 6     # shadow: nothing suppressed


# ── exclusion: safety / direct service calls bypass the gate entirely ────────

async def test_safety_direct_call_is_never_loop_suppressed(cog, act):
    # Even after a thrash loop is flagged on the autonomous detector, a direct
    # safety-style call (as _nighttime_lockdown does) still fires — it does not
    # route through _execute_action_data.
    for i in range(6):
        act.loop_detect_check("light.turn_on:light.lamp", enforce=True, now=5000.0)
    hass = FakeHass()
    await hass.services.async_call("lock", "lock", {"entity_id": "lock.front"},
                                   blocking=True)
    assert ("lock", "lock", {"entity_id": "lock.front"}) in hass.service_calls
