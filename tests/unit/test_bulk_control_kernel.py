"""MCU Phase B (B3) — bulk_control as an explicit plan with targets, routed
through the actuation envelope. Fire-and-forget (blocking=False) and the
protected-device skip are preserved; each executed target publishes an
actuation event."""
import json
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


@pytest.fixture
def published(load, monkeypatch):
    events = load("events")
    sink = []
    monkeypatch.setattr(events, "publish", lambda hass, ev: sink.append(ev))
    return sink


@pytest.fixture
def no_confirm(load, monkeypatch):
    policy = load("policy")
    monkeypatch.setattr(policy, "requires_confirmation", lambda *a, **k: False)
    return policy


@pytest.fixture
def authority_parity(load, monkeypatch):
    """Spy on the log-only authority-parity record (MCU #4b)."""
    ab = load("authority_bridge")
    calls = []
    monkeypatch.setattr(
        ab, "record_control_parity",
        lambda hass, d, s, **k: calls.append((d, s, k.get("allowed"), k.get("scope"))))
    return calls


async def test_bulk_turn_on_publishes_event_per_target(agent, fake_hass, no_confirm, published):
    fake_hass.states.set("light.a", "off")
    fake_hass.states.set("light.b", "off")
    out = await agent._exec_bulk_control(
        fake_hass, {"domain": "light", "action": "turn_on"})
    res = json.loads(out)
    assert res["count"] == 2 and res["total"] == 2
    assert len(published) == 2
    assert all(e.data["capability"] == "light.turn_on" for e in published)
    assert {e.subject for e in published} == {"light.a", "light.b"}


async def test_bulk_turn_off_filters_already_off(agent, fake_hass, no_confirm, published):
    fake_hass.states.set("light.a", "on")
    fake_hass.states.set("light.b", "off")   # already off → filtered out
    out = await agent._exec_bulk_control(
        fake_hass, {"domain": "light", "action": "turn_off"})
    res = json.loads(out)
    assert res["count"] == 1 and res["total"] == 1
    assert len(published) == 1 and published[0].subject == "light.a"


async def test_bulk_skips_protected_without_event(agent, fake_hass, load, monkeypatch, published):
    policy = load("policy")
    monkeypatch.setattr(policy, "requires_confirmation", lambda *a, **k: True)
    fake_hass.states.set("lock.front", "unlocked")
    fake_hass.states.set("lock.back", "unlocked")
    out = await agent._exec_bulk_control(
        fake_hass, {"domain": "lock", "action": "lock"})
    res = json.loads(out)
    assert res["count"] == 0 and res.get("blocked") == 2
    assert published == []   # protected devices never executed → no event


# ── authority: enforced per target (G4b), recorded per target (covers skipped) ──
async def test_bulk_enforces_an_engine_hold(
        agent, fake_hass, no_confirm, published, load, monkeypatch):
    # G4b: a target the engine holds — even one the legacy gate allows — is skipped
    # and reported blocked, with no actuation event (fails closed, like a
    # protected device).
    from types import SimpleNamespace
    ab = load("authority_bridge")
    monkeypatch.setattr(ab, "record_control_parity",
                        lambda *a, **k: SimpleNamespace(decision="confirm", reason="held"))
    fake_hass.states.set("light.ea", "off")
    fake_hass.states.set("light.eb", "off")
    out = await agent._exec_bulk_control(
        fake_hass, {"domain": "light", "action": "turn_on"})
    res = json.loads(out)
    assert res["count"] == 0 and res.get("blocked") == 2
    assert published == []


async def test_authority_parity_recorded_for_executed_targets(
        agent, fake_hass, no_confirm, published, authority_parity):
    fake_hass.states.set("light.a", "off")
    fake_hass.states.set("light.b", "off")
    await agent._exec_bulk_control(fake_hass, {"domain": "light", "action": "turn_on"})
    # one parity record per target, marked allowed (not held for confirmation)
    assert len(authority_parity) == 2
    assert all(c == ("light", "turn_on", True, s)
               for c, s in zip(authority_parity, ("light.a", "light.b")))


async def test_authority_parity_recorded_for_skipped_targets(
        agent, fake_hass, load, monkeypatch, published, authority_parity):
    policy = load("policy")
    monkeypatch.setattr(policy, "requires_confirmation", lambda *a, **k: True)
    fake_hass.states.set("lock.front", "unlocked")
    fake_hass.states.set("lock.back", "unlocked")
    await agent._exec_bulk_control(fake_hass, {"domain": "lock", "action": "lock"})
    # the skipped (protected) targets are still measured — allowed=False
    assert len(authority_parity) == 2
    assert all(allowed is False for (_d, _s, allowed, _scope) in authority_parity)
    assert published == []   # behaviour unchanged: nothing executed
