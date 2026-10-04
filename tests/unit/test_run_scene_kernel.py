"""MCU Phase B (B1) — run_scene_or_script routed through the actuation envelope.

Scenes/scripts/automations have no deterministic end-state, so there is no
verify/outcome; these pin the context read + actuation event (capability,
area) and that an invalid target still errors without touching the envelope."""
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


async def test_scene_activation_publishes_event(agent, fake_hass, published):
    fake_hass.states.set("scene.movie_night", "scening", area="living")
    out = await agent._exec_run_scene_script(
        fake_hass, {"entity_id": "scene.movie_night"})
    res = json.loads(out)
    assert res["success"] is True and res["area"] == "living"
    assert len(published) == 1
    ev = published[0]
    assert ev.type == "control.actuation"
    assert ev.data["capability"] == "scene.turn_on"
    assert ev.subject == "scene.movie_night"
    assert ev.location == "living"


async def test_script_uses_turn_on(agent, fake_hass, published):
    fake_hass.states.set("script.bedtime", "off")
    await agent._exec_run_scene_script(fake_hass, {"entity_id": "script.bedtime"})
    assert published[0].data["capability"] == "script.turn_on"
    # the service call itself is script.turn_on
    assert ("script", "turn_on") in [(c[0], c[1]) for c in fake_hass.service_calls]


async def test_automation_uses_trigger(agent, fake_hass, published):
    fake_hass.states.set("automation.away", "on")
    await agent._exec_run_scene_script(fake_hass, {"entity_id": "automation.away"})
    assert published[0].data["capability"] == "automation.trigger"


async def test_invalid_target_errors_without_envelope(agent, fake_hass, published):
    out = await agent._exec_run_scene_script(
        fake_hass, {"entity_id": "light.den"})
    assert "Not a scene/script/automation" in out
    assert published == []
