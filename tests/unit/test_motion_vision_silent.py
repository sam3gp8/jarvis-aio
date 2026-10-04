"""Silent generic-motion camera analysis (#140).

A generic HA motion sensor can trigger a camera vision analysis for users with
no Frigate/Nest, so best-effort face recognition has a frame to work on. That
trigger must run SILENTLY — the recognition hook inside async_analyze_camera
still fires, but no spoken announcement is added. async_auto_analyze_on_event's
`announce` flag is what carries that: announce=False must suppress the tts
target, the speaker list, and the call's own announce flag.
"""
import sys
import types

import pytest


@pytest.fixture
def cam(load, monkeypatch):
    if "aiohttp" not in sys.modules:
        monkeypatch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))
    return load("camera")


@pytest.fixture
def capture(cam, monkeypatch):
    """Replace async_analyze_camera with a capture, and skip the settle sleep."""
    seen = {}

    async def _fake_analyze(hass, call, client, honorific, tts, speakers, **kw):
        seen["call_data"] = dict(getattr(call, "data", {}) or {})
        seen["tts"] = tts
        seen["speakers"] = speakers
        return {"success": True, "analysis": "a person"}

    async def _no_sleep(*a, **k):
        return None

    monkeypatch.setattr(cam, "async_analyze_camera", _fake_analyze)
    monkeypatch.setattr(cam.asyncio, "sleep", _no_sleep)
    return seen


async def test_silent_motion_run_suppresses_announcement(cam, capture):
    await cam.async_auto_analyze_on_event(
        hass=object(), groq_client=None, honorific="sir",
        tts_entity="media_player.kitchen", speakers=["media_player.kitchen"],
        entity_id="camera.hallway", reason="Motion detected", announce=False,
    )
    # Silent: no tts target, no speakers, and the analysis call itself won't speak.
    assert capture["tts"] is None
    assert capture["speakers"] == []
    assert capture["call_data"].get("announce") is False
    # It still analysed the right camera (so the recognition hook gets a frame).
    assert capture["call_data"].get("entity_id") == "camera.hallway"


async def test_announcing_motion_run_keeps_targets(cam, capture):
    # Default announce=True preserves the tts target + speakers (unchanged path).
    await cam.async_auto_analyze_on_event(
        hass=object(), groq_client=None, honorific="sir",
        tts_entity="media_player.kitchen", speakers=["media_player.kitchen"],
        entity_id="camera.hallway", reason="Motion detected",
    )
    assert capture["tts"] == "media_player.kitchen"
    assert capture["speakers"] == ["media_player.kitchen"]
    assert capture["call_data"].get("announce") is True
