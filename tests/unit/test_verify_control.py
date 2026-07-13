"""Tests for verify-after-act (v6.38.0) — control actions confirm their outcome,
retry once, and report honestly. Stubs homeassistant.helpers.llm at import."""
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
def fast_sleep(agent, monkeypatch):
    calls = {"n": 0, "hook": None}
    async def _sleep(_secs):
        calls["n"] += 1
        if calls["hook"]:
            calls["hook"](calls["n"])
    monkeypatch.setattr(agent, "_VERIFY_SLEEP", _sleep)
    return calls


@pytest.fixture
def activity(load, monkeypatch):
    db = load("database")
    sink = []
    monkeypatch.setattr(db, "save_activity", lambda **kw: sink.append(kw))
    return sink


def _retries(fake_hass, domain, service):
    return [c for c in fake_hass.service_calls if c[0] == domain and c[1] == service]


async def test_persistent_failure_retries_once_and_logs(agent, fake_hass, fast_sleep, activity):
    fake_hass.states.set("cover.garage_door", "open")   # never reaches 'closed'
    out = await agent._exec_control_device(
        fake_hass, {"entity_id": "cover.garage_door", "action": "close"})
    assert '"success": true' in out.lower()
    await fake_hass.drain()                            # run the verify task
    assert len(_retries(fake_hass, "cover", "close_cover")) == 2   # act + 1 retry
    assert len(activity) == 1
    assert activity[0]["urgency"] == "medium"
    assert "did not respond" in activity[0]["message"]


async def test_success_first_try_is_silent(agent, fake_hass, fast_sleep, activity):
    fake_hass.states.set("light.den", "off")
    async def call_and_flip(domain, service, data=None, blocking=False, **kw):
        fake_hass.service_calls.append((domain, service, dict(data or {})))
        fake_hass.states.set("light.den", "on")
    fake_hass.services.async_call = call_and_flip
    await agent._exec_control_device(
        fake_hass, {"entity_id": "light.den", "action": "turn_on"})
    await fake_hass.drain()
    assert len(_retries(fake_hass, "light", "turn_on")) == 1       # no retry
    assert activity == []                                          # silent


async def test_recovery_on_retry_logs_low(agent, fake_hass, fast_sleep, activity):
    fake_hass.states.set("lock.front", "unlocked")
    # flip to locked during the post-retry sleep (2nd sleep on this path)
    fast_sleep["hook"] = (lambda n: fake_hass.states.set("lock.front", "locked")
                          if n >= 2 else None)
    await agent._exec_control_device(
        fake_hass, {"entity_id": "lock.front", "action": "lock"})
    await fake_hass.drain()
    assert len(_retries(fake_hass, "lock", "lock")) == 2
    assert len(activity) == 1 and activity[0]["urgency"] == "low"
    assert "retry" in activity[0]["message"]


async def test_transitional_state_gets_grace_then_passes(agent, fake_hass, fast_sleep, activity):
    fake_hass.states.set("cover.garage_door", "open")
    async def call_then_move(domain, service, data=None, blocking=False, **kw):
        fake_hass.service_calls.append((domain, service, dict(data or {})))
        fake_hass.states.set("cover.garage_door", "closing")
    fake_hass.services.async_call = call_then_move
    fast_sleep["hook"] = (lambda n: fake_hass.states.set("cover.garage_door", "closed")
                          if n >= 2 else None)
    await agent._exec_control_device(
        fake_hass, {"entity_id": "cover.garage_door", "action": "close"})
    await fake_hass.drain()
    assert len(_retries(fake_hass, "cover", "close_cover")) == 1   # no retry needed
    assert activity == []


async def test_non_deterministic_action_skips_verify(agent, fake_hass, fast_sleep):
    fake_hass.states.set("light.den", "on")
    await agent._exec_control_device(
        fake_hass, {"entity_id": "light.den", "action": "set_brightness", "value": 40})
    await fake_hass.drain()
    assert fast_sleep["n"] == 0                                    # no verify ran


def test_new_tools_registered(agent):
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert {"schedule_followup", "manage_followups"} <= names
    assert "schedule_followup" in agent._TOOL_MAP
    assert "manage_followups" in agent._TOOL_MAP


async def test_schedule_and_manage_exec_roundtrip(agent, load, fake_hass, tmp_path, monkeypatch):
    fu = load("followups")
    monkeypatch.setattr(fu, "DB_PATH", str(tmp_path / "p.db"))
    out = await agent._exec_schedule_followup(
        fake_hass, {"instruction": "check the oven", "delay_minutes": 10})
    assert "Follow-up #1 scheduled" in out
    listing = await agent._exec_manage_followups(fake_hass, {"action": "list"})
    assert "check the oven" in listing
    cancelled = await agent._exec_manage_followups(
        fake_hass, {"action": "cancel", "followup_id": 1})
    assert "#1 cancelled" in cancelled
