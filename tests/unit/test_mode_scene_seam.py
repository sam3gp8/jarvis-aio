"""MCU Phase H (H11): movie-mode mood dim routes through the universal seam.

``mode_scene.apply_mode_entry`` dims the bound room's lights when Movie mode
activates. It used a direct multi-entity ``hass.services.async_call``; H11 routes
it per-entity through ``actuation.execute_actuator`` so the mood actuation is
planned, event-published and journaled like every other actuation (still
fire-and-forget, same net effect).
"""
import pytest

from fakes import FakeHass


@pytest.fixture
def ms(load, monkeypatch):
    mode_scene = load("mode_scene")
    cfg = load("jarvis_config")
    ar = load("audio_routing")
    monkeypatch.setattr(cfg, "get", lambda k, d=None: {
        "movie_area": "office", "movie_dim_pct": 15,
    }.get(k, d))
    monkeypatch.setattr(ar, "entity_area",
                        lambda hass, eid: "office" if eid.startswith("light.") else None)
    return mode_scene


async def test_movie_dim_routes_each_light_through_seam(ms, load, monkeypatch):
    ev = load("events")
    published = []
    monkeypatch.setattr(ev, "publish", lambda hass, e: published.append(e))
    hass = FakeHass()
    hass.states.set("light.a", "on")
    hass.states.set("light.b", "on")

    await ms.apply_mode_entry(hass, "movie")

    # per-entity turn_on with the dim level (not one multi-entity batch)
    assert ("light", "turn_on", {"entity_id": "light.a", "brightness_pct": 15}) in hass.service_calls
    assert ("light", "turn_on", {"entity_id": "light.b", "brightness_pct": 15}) in hass.service_calls
    caps = {(e.data.get("capability"), e.subject) for e in published}
    assert ("light.turn_on", "light.a") in caps
    assert ("light.turn_on", "light.b") in caps


async def test_movie_dim_zero_turns_off_through_seam(ms, load, monkeypatch):
    cfg = load("jarvis_config")
    monkeypatch.setattr(cfg, "get", lambda k, d=None: {
        "movie_area": "office", "movie_dim_pct": 0,
    }.get(k, d))
    hass = FakeHass()
    hass.states.set("light.a", "on")
    await ms.apply_mode_entry(hass, "movie")
    assert ("light", "turn_off", {"entity_id": "light.a"}) in hass.service_calls


async def test_non_movie_mode_is_a_noop(ms):
    hass = FakeHass()
    hass.states.set("light.a", "on")
    await ms.apply_mode_entry(hass, "home")
    assert hass.service_calls == []
