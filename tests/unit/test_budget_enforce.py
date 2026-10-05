"""MCU Phase G (G1): the agency budget is ENFORCED on the discretionary
autonomous actuation path — the first decision primitive promoted from shadow
to enforce on the live home.

When JARVIS acts on a learned/trusted pattern of its own accord
(`cognitive_core._execute_action_data`) and its self-imposed hourly ceiling of
autonomous actions is reached, the actuation is actually BLOCKED (not merely
logged). Crucially, the gate is tightly scoped:

  * user-requested actuations (the agent tool path) never reach it, and
  * safety-critical responses (nighttime lockdown, intrusion securing) call
    hass.services directly and never route through it,

so neither a user command nor a safety action can ever be budget-blocked. The
gate fails open, and a one-line kill-switch (`AGENCY_BUDGET_ENFORCE`) reverts it
to shadow.
"""
import logging

import pytest

from fakes import FakeHass


@pytest.fixture
def act(load):
    mod = load("actuation")
    mod._autonomous_budget = None           # fresh budget per test
    mod._loop_detector = None
    mod._agency_budget = None
    return mod


# ── the enforce gate itself ─────────────────────────────────────────────────

def test_gate_allows_under_ceiling_and_records(act):
    # Default ceiling is 60/hr; the first call is allowed and reports slots left.
    allowed, remaining = act.agency_budget_check(enforce=True, now=1000.0)
    assert allowed is True
    assert remaining == 59          # consumed one of 60


def test_gate_blocks_at_ceiling_when_enforcing(act):
    # Fill the window to the cap, then the next autonomous action is BLOCKED.
    for i in range(60):
        allowed, _ = act.agency_budget_check(enforce=True, now=1000.0 + i)
        assert allowed is True
    allowed, remaining = act.agency_budget_check(enforce=True, now=1060.0)
    assert allowed is False
    assert remaining == 0


def test_gate_does_not_block_when_kill_switch_off(act, caplog):
    # With enforce=False the ceiling is a shadow log — it NEVER blocks.
    for i in range(60):
        act.agency_budget_check(enforce=False, now=2000.0 + i)
    with caplog.at_level(logging.WARNING,
                         logger="custom_components.jarvis.actuation"):
        allowed, remaining = act.agency_budget_check(enforce=False, now=2060.0)
    assert allowed is True          # shadow: let it through
    assert remaining == 0
    assert any("NOT blocked" in r.message for r in caplog.records)


def test_gate_blocked_action_consumes_no_slot(act):
    # A blocked action must not record — so once the window frees up, actions
    # flow again rather than the budget being permanently poisoned.
    for i in range(60):
        act.agency_budget_check(enforce=True, now=3000.0 + i)
    # blocked (does not record)
    assert act.agency_budget_check(enforce=True, now=3060.0)[0] is False
    # far enough ahead that the whole window has rolled off → allowed again
    allowed, _ = act.agency_budget_check(enforce=True, now=3000.0 + 3700.0)
    assert allowed is True


def test_gate_fails_open_on_error(act, monkeypatch):
    # If the budget machinery blows up, the gate must ALLOW — a budget bug can
    # never stop JARVIS from acting.
    import time as _t
    monkeypatch.setattr(act, "_autonomous_budget", None)

    class _Boom:
        def check_and_record(self, *a, **k):
            raise RuntimeError("boom")
    # Force the lazily-created budget to be our exploding one.
    monkeypatch.setattr(act, "_autonomous_budget", _Boom())
    allowed, remaining = act.agency_budget_check(enforce=True, now=_t.time())
    assert allowed is True
    assert remaining is None


def test_default_kill_switch_is_enforce(act):
    # The owner approved the staged roll-out: enforce is ON by default.
    assert act.AGENCY_BUDGET_ENFORCE is True


# ── the live autonomous path honors the gate ────────────────────────────────

@pytest.fixture
def cog(load, act):
    # Loading actuation first (via `act`) guarantees the same module object the
    # autonomous path imports is the one whose budget we reset.
    return load("cognitive_core")


async def test_autonomous_action_executes_under_ceiling(cog):
    hass = FakeHass()
    hass.states.set("light.lamp", "off")  # entity exists (seam precondition)
    ok = await cog._execute_action_data(
        hass, {"domain": "light", "service": "turn_on",
               "entity_ids": ["light.lamp"]})
    assert ok is True
    # H5: the proactive actuation now routes through the universal seam per target
    # (canonical per-entity ActuatorRequest), so the service call is per-entity.
    assert hass.service_calls == [("light", "turn_on", {"entity_id": "light.lamp"})]


async def test_proactive_action_routes_through_seam_and_emits_event(cog, load, monkeypatch):
    # H5: the proactive actuation goes through the universal seam, so it publishes
    # a canonical actuation JarvisEvent (the same observability as control_device).
    ev = load("events")
    published = []
    monkeypatch.setattr(ev, "publish", lambda hass, e: published.append(e))
    hass = FakeHass()
    hass.states.set("light.lamp", "off")
    ok = await cog._execute_action_data(
        hass, {"domain": "light", "service": "turn_on", "entity_ids": ["light.lamp"]})
    assert ok is True
    assert len(published) == 1
    assert published[0].data["capability"] == "light.turn_on"
    assert published[0].subject == "light.lamp"


async def test_autonomous_action_blocked_when_budget_exhausted(cog, act, caplog):
    hass = FakeHass()
    hass.states.set("light.lamp", "off")  # entity exists (seam precondition)
    # Exhaust the autonomous budget so the next proactive action is over-ceiling.
    import time
    now = time.time()
    for i in range(60):
        act.agency_budget_check(enforce=True, now=now)
    with caplog.at_level(logging.WARNING,
                         logger="custom_components.jarvis.cognitive_core"):
        ok = await cog._execute_action_data(
            hass, {"domain": "light", "service": "turn_on",
                   "entity_ids": ["light.lamp"]})
    assert ok is False
    assert hass.service_calls == []         # the service call was SUPPRESSED
    assert any("suppressed by agency budget" in r.message for r in caplog.records)


async def test_autonomous_action_not_blocked_with_kill_switch_off(cog, act, monkeypatch):
    # Flip the kill-switch: the exact same over-ceiling situation lets the
    # action through (shadow), proving the revert path.
    monkeypatch.setattr(act, "AGENCY_BUDGET_ENFORCE", False)
    hass = FakeHass()
    hass.states.set("light.lamp", "off")  # entity exists (seam precondition)
    import time
    now = time.time()
    for i in range(60):
        act.agency_budget_check(now=now)    # uses the (now-off) module default
    ok = await cog._execute_action_data(
        hass, {"domain": "light", "service": "turn_on",
               "entity_ids": ["light.lamp"]})
    assert ok is True
    assert hass.service_calls == [("light", "turn_on", {"entity_id": "light.lamp"})]


# ── exclusion: safety / direct service calls bypass the gate entirely ────────

async def test_safety_direct_call_is_never_budget_blocked(cog, act):
    # Safety responses (lockdown, intrusion securing) call hass.services directly
    # — they do NOT go through _execute_action_data, so even with the autonomous
    # budget fully exhausted, a direct lock call still fires.
    import time
    now = time.time()
    for i in range(60):
        act.agency_budget_check(enforce=True, now=now)   # exhaust autonomous budget
    hass = FakeHass()
    hass.states.set("light.lamp", "off")  # entity exists (seam precondition)
    # Simulate the safety path (as _nighttime_lockdown does): a direct call.
    await hass.services.async_call("lock", "lock", {"entity_id": "lock.front"},
                                   blocking=True)
    assert ("lock", "lock", {"entity_id": "lock.front"}) in hass.service_calls
