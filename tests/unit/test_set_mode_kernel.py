"""MCU Phase B (B2) — set_mode records the mode change through the actuation
envelope. set_mode is a directive (its home effect is the applied mode scene),
not a single-entity actuation, so there is no WorldModel context and no
verify/outcome; the actuation event's target is the mode name."""
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
def modes_wired(load, monkeypatch):
    modes = load("modes")
    mode_scene = load("mode_scene")
    monkeypatch.setattr(modes, "mode_info", lambda: {"description": "test mode"})
    async def _noop(hass, mode):
        return None
    monkeypatch.setattr(mode_scene, "apply_mode_entry", _noop)
    return modes


async def test_set_mode_publishes_actuation_event(agent, fake_hass, modes_wired,
                                                  monkeypatch, published):
    monkeypatch.setattr(modes_wired, "set_mode", lambda m, r: {"ok": True, "mode": m})
    out = await agent._exec_set_mode(
        fake_hass, {"mode": "away", "reason": "leaving"})
    res = json.loads(out)
    assert res["ok"] is True and res["mode"] == "away"
    assert len(published) == 1
    ev = published[0]
    assert ev.type == "control.actuation"
    assert ev.data["capability"] == "jarvis.set_mode"
    assert ev.subject == "away"
    assert ev.data["intent"] == "set mode"


async def test_set_mode_failure_publishes_nothing(agent, fake_hass, modes_wired,
                                                  monkeypatch, published):
    monkeypatch.setattr(modes_wired, "set_mode",
                        lambda m, r: {"ok": False, "error": "unknown mode"})
    out = await agent._exec_set_mode(fake_hass, {"mode": "bogus"})
    assert '"ok": false' in out.lower()
    assert published == []


async def test_set_mode_runs_through_the_kernel_planner(
        agent, fake_hass, modes_wired, load, monkeypatch, published):
    # Stage 2 (plan → full): the directive's application EXECUTES as a one-step
    # kernel plan (aexecute_plan) — planner-owned and journaled, not a shadow.
    monkeypatch.setattr(modes_wired, "set_mode", lambda m, r: {"ok": True, "mode": m})
    kplan = load("kernel.plan")
    seen = {}
    real = kplan.aexecute_plan

    async def _spy(plan, **kw):
        seen["goal"] = plan.goal
        seen["steps"] = [s.action for s in plan.steps]
        return await real(plan, **kw)

    monkeypatch.setattr(kplan, "aexecute_plan", _spy)
    out = await agent._exec_set_mode(fake_hass, {"mode": "movie"})
    res = json.loads(out)
    assert res["ok"] is True and res["mode"] == "movie"
    assert seen.get("steps") == ["jarvis.set_mode"]   # executed through the planner
    assert "movie" in seen.get("goal", "")
    assert len(published) == 1                          # the actuation event still fires


async def test_set_mode_request_target_is_normalized(
        agent, fake_hass, modes_wired, load, monkeypatch, published):
    """The recorded ActuatorRequest must key off the CANONICAL (normalized) mode,
    matching the emitted event's target. A non-canonical name ("  Movie  ")
    previously built the request + idempotency key from the raw arg while the
    event used the normalized "movie", breaking request↔event correlation and
    idempotent dedup."""
    # set_mode normalizes exactly like the real one (.strip().lower()).
    monkeypatch.setattr(modes_wired, "set_mode",
                        lambda m, r: {"ok": True, "mode": str(m).strip().lower()})
    actuation = load("actuation")
    req_targets = []
    real_request = actuation.request

    def _spy_request(capability, entity_id, **kw):
        req_targets.append(entity_id)
        return real_request(capability, entity_id, **kw)

    monkeypatch.setattr(actuation, "request", _spy_request)

    out = await agent._exec_set_mode(fake_hass, {"mode": "  Movie  "})
    res = json.loads(out)
    assert res["ok"] is True and res["mode"] == "movie"
    # request target == canonical mode == event subject (no raw/normalized split)
    assert req_targets == ["movie"]
    assert len(published) == 1 and published[0].subject == "movie"


async def test_set_mode_survives_a_planner_fault(
        agent, fake_hass, modes_wired, load, monkeypatch):
    # Fail-safe: a planner fault must never prevent the mode change.
    applied = {}
    monkeypatch.setattr(modes_wired, "set_mode",
                        lambda m, r: applied.setdefault("m", m) and None or {"ok": True, "mode": m})
    kplan = load("kernel.plan")

    async def _boom(plan, **kw):
        raise RuntimeError("planner down")
    monkeypatch.setattr(kplan, "aexecute_plan", _boom)

    out = await agent._exec_set_mode(fake_hass, {"mode": "away"})
    res = json.loads(out)
    assert res["ok"] is True and res["mode"] == "away"   # mode still set via fail-safe
