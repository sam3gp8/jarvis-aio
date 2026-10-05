"""MCU Phase H (H6) — delegation attribution.

When JARVIS delegates an objective to a named sub-agent (FRIDAY the background
automator), the actuations the sub-agent performs flow through the very same
universal seam JARVIS uses (control_device / bulk_control / run_scene_or_script).
Without help the journal would record *"jarvis did X"* for an action FRIDAY took
under JARVIS's delegation.

H6 threads the acting agent through a kernel ``actor`` contextvar: ``_run_delegated``
brackets the sub-agent's run in an ``actor.scope("friday")`` so every ActuatorRequest
it builds — and every actuation JarvisEvent it emits — is attributed to the
sub-agent, while the existing correlation id still links the chain back to JARVIS's
delegating turn. Metadata-only (the recorded ``actor``), behaviour-preserving, and
kill-switchable (``agent._DELEGATION_ATTRIBUTION``). A generic capability-scoped
delegation is JARVIS with a reduced toolset, so it stays ``"jarvis"``.
"""
import json

import pytest


# ── the kernel actor contextvar ──────────────────────────────────────────────

@pytest.fixture
def actor(load):
    # Import through the real kernel package so its __init__ runs and we get the
    # very same actor module object that actuation.acting_agent() reads — not a
    # bare intermediate (which _load("kernel.actor") would register, shadowing
    # `from .kernel import build_actuator_request`).
    load("actuation")                       # ensure the jc.* stub tree is set up
    from jc.kernel import actor as _actor
    return _actor


def test_actor_defaults_to_jarvis(actor):
    assert actor.current() == "jarvis"


def test_actor_scope_sets_and_restores(actor):
    assert actor.current() == "jarvis"
    with actor.scope("friday"):
        assert actor.current() == "friday"
    assert actor.current() == "jarvis"        # restored on exit


def test_actor_scope_falsy_is_passthrough(actor):
    with actor.scope("homer"):
        with actor.scope(""):                 # no-op: keeps the outer actor
            assert actor.current() == "homer"
        assert actor.current() == "homer"


def test_actor_scope_nests(actor):
    with actor.scope("friday"):
        with actor.scope("homer"):
            assert actor.current() == "homer"
        assert actor.current() == "friday"    # inner reset, outer intact


# ── actuation reads the ambient actor ────────────────────────────────────────

@pytest.fixture
def actuation(load):
    return load("actuation")


def test_acting_agent_is_jarvis_by_default(actuation):
    assert actuation.acting_agent() == "jarvis"


def test_request_actor_is_jarvis_on_direct_path(actuation):
    req = actuation.request("light.turn_on", "light.den", action="turn_on")
    assert req is not None
    assert req.actor == "jarvis"


def test_request_actor_follows_scope(actuation, actor):
    with actor.scope("friday"):
        req = actuation.request("light.turn_on", "light.den", action="turn_on")
    assert req is not None
    assert req.actor == "friday"              # FRIDAY's canonical request


def test_emit_event_attributes_actor_to_the_sub_agent(actuation, load, fake_hass,
                                                       monkeypatch, actor):
    events = load("events")
    sink = []
    monkeypatch.setattr(events, "publish", lambda hass, ev: sink.append(ev))
    with actor.scope("friday"):
        req = actuation.request("light.turn_on", "light.den", action="turn_on")
        actuation.emit_event(fake_hass, "light.turn_on", "light.den",
                             action="turn_on", area="den", request=req)
    assert len(sink) == 1
    assert sink[0].data["actor"] == "friday"  # the event names who acted


def test_emit_event_actor_defaults_to_jarvis(actuation, load, fake_hass, monkeypatch):
    events = load("events")
    sink = []
    monkeypatch.setattr(events, "publish", lambda hass, ev: sink.append(ev))
    req = actuation.request("light.turn_on", "light.den", action="turn_on")
    actuation.emit_event(fake_hass, "light.turn_on", "light.den",
                         action="turn_on", request=req)
    assert sink[0].data["actor"] == "jarvis"


# ── _run_delegated brackets the sub-agent in an actor scope ───────────────────

@pytest.fixture
def agent(load):
    return load("agent")


@pytest.fixture
def friday_on(agent, load, monkeypatch):
    cfg = load("jarvis_config")
    monkeypatch.setattr(cfg, "get",
                        lambda k, d=None: True if k == "friday_automator" else d)
    return agent


@pytest.fixture
def spy_actor_during_run(agent, load, monkeypatch):
    """Spy run_agent that records the ambient actor at the moment it runs — i.e.
    the attribution a sub-agent's actuations would inherit."""
    from jc.kernel import actor
    seen = {}

    async def _fake(hass, **kw):
        seen["actor"] = actor.current()
        return "done"

    monkeypatch.setattr(agent, "run_agent", _fake)
    return seen


async def _delegate(agent, args, depth=0):
    return await agent._run_delegated(
        None, args, persona="p", provider_name="ollama", api_key="",
        model="gemma4:26b", base_url=None, config={}, depth=depth,
    )


async def test_friday_run_is_attributed_to_friday(friday_on, spy_actor_during_run):
    out = json.loads(await _delegate(
        friday_on, {"objective": "run the movie scene", "profile": "FRIDAY"}))
    assert out["profile"] == "FRIDAY"
    assert spy_actor_during_run["actor"] == "friday"


async def test_homer_run_is_attributed_to_homer(agent, spy_actor_during_run):
    await _delegate(agent, {"objective": "why is the lamp offline", "profile": "HOMER"})
    assert spy_actor_during_run["actor"] == "homer"


async def test_generic_capability_delegation_stays_jarvis(agent, spy_actor_during_run):
    # A capability-scoped delegation is JARVIS with a reduced toolset, not a named
    # agent — attribution stays "jarvis".
    await _delegate(agent, {"objective": "check my week", "capability": "scheduling"})
    assert spy_actor_during_run["actor"] == "jarvis"


async def test_actor_restored_after_delegated_run(friday_on, spy_actor_during_run, load):
    from jc.kernel import actor
    await _delegate(friday_on, {"objective": "x", "profile": "FRIDAY"})
    assert actor.current() == "jarvis"        # scope did not leak past the run


async def test_kill_switch_reverts_to_jarvis(friday_on, spy_actor_during_run, monkeypatch):
    monkeypatch.setattr(friday_on, "_DELEGATION_ATTRIBUTION", False)
    await _delegate(friday_on, {"objective": "x", "profile": "FRIDAY"})
    assert spy_actor_during_run["actor"] == "jarvis"   # attribution disabled
