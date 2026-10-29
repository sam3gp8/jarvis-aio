"""Authority-breadth (9.34.0): the discretionary *autonomous* actuation path
records the kernel authority engine's verdict (parity, log-only).

`cognitive_core._execute_action_data` is JARVIS acting on a learned/trusted
pattern of its own accord — the class of action whose authority most needs to be
measured. It already routes each target through the universal actuation seam
(so plan/event are wired); this adds the authority cell at parity: per target it
records the engine verdict (`authority_bridge.record_control_parity`) against the
path's proceed decision (the G1/G2 gates already let it through). Strictly
log-only — it never gates the action, and a fault in it never disrupts the
action.
"""
import logging

import pytest

from fakes import FakeHass


@pytest.fixture
def act(load):
    # Reset the autonomous budget / loop detector so the gates let actions
    # through (same discipline as test_budget_enforce).
    mod = load("actuation")
    mod._autonomous_budget = None
    mod._loop_detector = None
    mod._agency_budget = None
    return mod


@pytest.fixture
def cog(load, act):
    # Load actuation (via `act`) first so the autonomous path imports the same
    # module object whose budget we reset.
    load("authority_bridge")
    return load("cognitive_core")


def _capture_parity(load, monkeypatch):
    from types import SimpleNamespace
    calls = []
    ab = load("authority_bridge")

    def _rec(hass, domain, service, *, allowed, scope=None, intent=None, **kw):
        calls.append({"domain": domain, "service": service, "allowed": allowed,
                      "scope": scope, "intent": intent})
        return SimpleNamespace(decision="confirm", reason="test")  # would-hold
    monkeypatch.setattr(ab, "record_control_parity", _rec)
    return calls


async def test_proactive_action_records_authority_parity(cog, load, monkeypatch):
    # Namespaced entity id (`light.pa_*`): the loop detector keys on
    # domain.service:entities, and the module-level detector is shared across
    # the suite, so a test must not reuse another test's key (e.g. `light.lamp`)
    # or it inflates that key's thrash count across files.
    calls = _capture_parity(load, monkeypatch)
    hass = FakeHass()
    hass.states.set("light.pa_one", "off")
    ok = await cog._execute_action_data(
        hass, {"domain": "light", "service": "turn_on",
               "entity_ids": ["light.pa_one"]})
    # Parity recorded for the right capability + target, tagged proactive.
    assert len(calls) == 1
    assert calls[0]["domain"] == "light"
    assert calls[0]["service"] == "turn_on"
    assert calls[0]["allowed"] is True
    assert calls[0]["scope"] == "light.pa_one"
    assert calls[0]["intent"] == "proactive"
    # LOG-ONLY: the engine "would confirm", yet the action still ran and the
    # service call fired — the parity record never gates.
    assert ok is True
    assert hass.service_calls == [("light", "turn_on", {"entity_id": "light.pa_one"})]


async def test_parity_recorded_once_per_target(cog, load, monkeypatch):
    calls = _capture_parity(load, monkeypatch)
    hass = FakeHass()
    eids = ["light.pa_a", "light.pa_b", "light.pa_c"]
    for eid in eids:
        hass.states.set(eid, "off")
    await cog._execute_action_data(
        hass, {"domain": "light", "service": "turn_on", "entity_ids": eids})
    # One authority record per entity (the seam actuates per target).
    assert [c["scope"] for c in calls] == eids
    assert all(c["intent"] == "proactive" for c in calls)


async def test_parity_fault_never_disrupts_the_action(cog, load, monkeypatch):
    ab = load("authority_bridge")

    def _boom(*a, **k):
        raise RuntimeError("parity backend down")
    monkeypatch.setattr(ab, "record_control_parity", _boom)

    hass = FakeHass()
    hass.states.set("light.pa_fault", "off")
    ok = await cog._execute_action_data(
        hass, {"domain": "light", "service": "turn_on",
               "entity_ids": ["light.pa_fault"]})
    # A fault in the log-only parity path is swallowed; the action still fires.
    assert ok is True
    assert hass.service_calls == [
        ("light", "turn_on", {"entity_id": "light.pa_fault"})]


async def test_parity_recorded_before_the_gates_pass_only(cog, load, monkeypatch):
    # A suppressed action (empty/invalid) records no parity — the record sits on
    # the real per-target actuation loop, past the early-return guards.
    calls = _capture_parity(load, monkeypatch)
    hass = FakeHass()
    ok = await cog._execute_action_data(hass, {})          # no domain/service
    assert ok is False
    assert calls == []


# ── verify-after-act on the autonomous path (9.36.0) ─────────────────────────
# When JARVIS acts of its own accord on a deterministic target, it reads the
# target back and records the canonical ActuatorOutcome: VERIFIED when the world
# reached the expected state, MISMATCH when it did not — the audit's point 18,
# where it matters most (no human is watching an autonomous action).

async def _no_sleep(*_a, **_k):
    return None


def _capture_outcomes(load, monkeypatch):
    seen = []
    act = load("actuation")

    def _rec(request, status, hass, entity_id, detail=""):
        seen.append((entity_id, status))
    monkeypatch.setattr(act, "outcome", _rec)
    return seen


async def test_verify_records_verified_when_state_reaches_expected(
        cog, load, monkeypatch):
    outcomes = _capture_outcomes(load, monkeypatch)
    monkeypatch.setattr(cog, "_PROACTIVE_VERIFY_SLEEP", _no_sleep)
    hass = FakeHass()
    hass.states.set("light.pv_ok", "off")
    ok = await cog._execute_action_data(
        hass, {"domain": "light", "service": "turn_on",
               "entity_ids": ["light.pv_ok"]})
    assert ok is True
    # Simulate HA applying the service (the fake service call doesn't), then run
    # the scheduled verify.
    hass.states.set("light.pv_ok", "on")
    await hass.drain()
    assert (outcomes[-1] if outcomes else None) == ("light.pv_ok", cog._P_VERIFIED)


async def test_verify_records_mismatch_when_state_does_not_reach(
        cog, load, monkeypatch):
    outcomes = _capture_outcomes(load, monkeypatch)
    monkeypatch.setattr(cog, "_PROACTIVE_VERIFY_SLEEP", _no_sleep)
    hass = FakeHass()
    hass.states.set("light.pv_miss", "off")                # stays off
    await cog._execute_action_data(
        hass, {"domain": "light", "service": "turn_on",
               "entity_ids": ["light.pv_miss"]})
    await hass.drain()                                     # run the scheduled verify
    # The world never reached "on", so the outcome is a mismatch (recorded, not
    # silently re-fired).
    assert ("light.pv_miss", cog._P_MISMATCH) in outcomes


async def test_nondeterministic_service_gets_no_verify(cog, load, monkeypatch):
    # A service with no single expected end-state (e.g. set_temperature) schedules
    # no verify and records no outcome — exactly like control_device.
    outcomes = _capture_outcomes(load, monkeypatch)
    monkeypatch.setattr(cog, "_PROACTIVE_VERIFY_SLEEP", _no_sleep)
    hass = FakeHass()
    hass.states.set("climate.den", "heat")
    ok = await cog._execute_action_data(
        hass, {"domain": "climate", "service": "set_temperature",
               "entity_ids": ["climate.den"],
               "service_data": {"temperature": 70}})
    assert ok is True
    await hass.drain()
    assert outcomes == []


async def test_verify_fault_never_disrupts_the_action(cog, load, monkeypatch):
    # A fault inside the scheduled verify is swallowed — it runs after the action
    # has already succeeded and must never surface.
    act = load("actuation")

    def _boom(*_a, **_k):
        raise RuntimeError("outcome backend down")
    monkeypatch.setattr(act, "outcome", _boom)
    monkeypatch.setattr(cog, "_PROACTIVE_VERIFY_SLEEP", _no_sleep)
    hass = FakeHass()
    hass.states.set("light.pv_boom", "off")
    ok = await cog._execute_action_data(
        hass, {"domain": "light", "service": "turn_on",
               "entity_ids": ["light.pv_boom"]})
    assert ok is True
    await hass.drain()          # must not raise
    assert hass.service_calls == [
        ("light", "turn_on", {"entity_id": "light.pv_boom"})]
