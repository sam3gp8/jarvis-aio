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
