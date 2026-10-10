"""Learned presence-routine recall — the usual_times tool (#341-style quality).

cognition.presence_schedule surfaces the learned usual leave/arrive times per
presence entity; agent._exec_usual_times is the tool the chat brain calls for
"what time do I usually get home?" questions, with a clear "not learned yet"
answer when there's no routine. Also covers the empty-reply fallback so the chat
never returns a blank turn.
"""
import json

import pytest


# ── cognition.presence_schedule ─────────────────────────────────────────────
@pytest.fixture
def cog(load):
    c = load("cognition")
    c._MODEL.clear()
    yield c
    c._MODEL.clear()


def _entry(cog, now=0.0, *, depart=None, ret=None):
    e = cog._Entry(now)
    for day, secs in (depart or []):
        e.depart_first.append((day, secs))
    for day, secs in (ret or []):
        e.return_first.append((day, secs))
    return e


def test_presence_schedule_reports_learned_routine(cog):
    # 10 consecutive days, leave 08:00 (28800s), home 17:30 (63000s) — consistent.
    depart = [(d, 28800) for d in range(10)]
    ret = [(d, 63000) for d in range(10)]
    cog._MODEL["device_tracker.sam_s_jeep"] = _entry(cog, depart=depart, ret=ret)

    rows = cog.presence_schedule()
    assert len(rows) == 1
    r = rows[0]
    assert r["entity_id"] == "device_tracker.sam_s_jeep"
    assert r["usual_depart"] == "08:00"
    assert r["usual_return"] == "17:30"
    assert r["depart_samples"] == 10 and r["return_samples"] == 10


def test_presence_schedule_empty_when_not_enough_days(cog):
    # Only 3 days — below RECUR_MIN_DAYS, so no routine.
    cog._MODEL["person.sam"] = _entry(cog, ret=[(d, 63000) for d in range(3)])
    assert cog.presence_schedule() == []


def test_presence_schedule_ignores_non_presence_entities(cog):
    cog._MODEL["light.kitchen"] = _entry(cog, ret=[(d, 63000) for d in range(10)])
    assert cog.presence_schedule() == []


def test_presence_schedule_skips_inconsistent_times(cog):
    # Arrival all over the clock (huge spread) → not a routine.
    ret = [(d, (d * 5000) % 86400) for d in range(10)]
    cog._MODEL["person.sam"] = _entry(cog, ret=ret)
    assert cog.presence_schedule() == []


# ── agent._exec_usual_times ─────────────────────────────────────────────────
@pytest.fixture
def agent(load):
    return load("agent")


async def test_exec_usual_times_learned(agent, fake_hass, monkeypatch):
    from jc import cognition
    monkeypatch.setattr(cognition, "presence_schedule", lambda hass=None: [
        {"entity_id": "device_tracker.sam_s_jeep", "name": "Sam's Jeep",
         "usual_depart": "08:00", "usual_return": "17:30"}])
    out = json.loads(await agent._exec_usual_times(fake_hass, {}))
    assert out["learned"] is True
    assert out["routines"][0]["usual_return"] == "17:30"


async def test_exec_usual_times_not_learned(agent, fake_hass, monkeypatch):
    from jc import cognition
    monkeypatch.setattr(cognition, "presence_schedule", lambda hass=None: [])
    out = json.loads(await agent._exec_usual_times(fake_hass, {}))
    assert out["learned"] is False
    assert "learned yet" in out["detail"]


async def test_exec_usual_times_filters_by_who(agent, fake_hass, monkeypatch):
    from jc import cognition
    monkeypatch.setattr(cognition, "presence_schedule", lambda hass=None: [
        {"entity_id": "device_tracker.sam_s_jeep", "name": "Sam's Jeep",
         "usual_return": "17:30"},
        {"entity_id": "person.alex", "name": "Alex", "usual_return": "18:15"}])
    out = json.loads(await agent._exec_usual_times(fake_hass, {"who": "alex"}))
    assert out["learned"] is True
    assert len(out["routines"]) == 1 and out["routines"][0]["name"] == "Alex"


async def test_exec_usual_times_who_not_found(agent, fake_hass, monkeypatch):
    from jc import cognition
    monkeypatch.setattr(cognition, "presence_schedule", lambda hass=None: [
        {"entity_id": "person.sam", "name": "Sam", "usual_return": "17:30"}])
    out = json.loads(await agent._exec_usual_times(fake_hass, {"who": "nobody"}))
    assert out["learned"] is False


async def test_exec_usual_times_defensive(agent, fake_hass, monkeypatch):
    from jc import cognition
    def boom(hass=None):
        raise RuntimeError("cognition down")
    monkeypatch.setattr(cognition, "presence_schedule", boom)
    out = json.loads(await agent._exec_usual_times(fake_hass, {}))
    assert out["learned"] is False and "error" in out


# ── the tool is wired + the empty-reply fallback ─────────────────────────────
def test_usual_times_is_registered(agent):
    assert "usual_times" in agent._TOOL_MAP
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert "usual_times" in names


def test_empty_reply_fallback_honours_honorific(agent):
    assert "boss" in agent._empty_reply_fallback({"honorific": "boss"})
    assert "sir" in agent._empty_reply_fallback({})
    assert "sir" in agent._empty_reply_fallback(None)   # never raises
