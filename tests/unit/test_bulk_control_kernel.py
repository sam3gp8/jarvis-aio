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
