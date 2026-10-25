"""Discretionary camera-vision rate limit (#421).

Automatic / event-triggered camera analyses (generic HA motion, auto event
reviews) can exhaust a vision provider's daily token budget when they fire
often. `vision_min_interval` (seconds; 0 = off) sets a global minimum gap
between those DISCRETIONARY analyses — explicit calls (the analyze_camera
service / agent tool), doorbell presses and person-confirmation are never
throttled. These tests pin that behaviour: the slot logic, the short-circuit in
async_analyze_camera (which must skip BEFORE any capture/vision), and that the
auto-event wrapper opts into throttling while the default path does not.
"""
import sys
import types

import pytest


@pytest.fixture
def cam(load, monkeypatch):
    if "aiohttp" not in sys.modules:
        monkeypatch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))
    return load("camera")


def _set_interval(cam, monkeypatch, seconds):
    """Pin vision_min_interval and reset the last-analysis clock."""
    real = cam._cfg_opt

    def _fake(hass, key, default=None):
        if key == "vision_min_interval":
            return seconds
        return real(hass, key, default)
    monkeypatch.setattr(cam, "_cfg_opt", _fake)
    monkeypatch.setattr(cam, "_last_auto_vision_ts", float("-inf"))


# ── the slot logic ───────────────────────────────────────────────────────────

def test_zero_interval_always_allows(cam, monkeypatch):
    _set_interval(cam, monkeypatch, 0)
    h = object()
    assert cam._claim_auto_vision_slot(h, now=100.0) is True
    assert cam._claim_auto_vision_slot(h, now=100.0) is True  # no gap enforced


def test_interval_blocks_within_window_and_allows_after(cam, monkeypatch):
    _set_interval(cam, monkeypatch, 300)
    h = object()
    assert cam._claim_auto_vision_slot(h, now=1000.0) is True   # first claims
    assert cam._claim_auto_vision_slot(h, now=1200.0) is False  # +200s < 300
    assert cam._claim_auto_vision_slot(h, now=1299.9) is False  # still inside
    assert cam._claim_auto_vision_slot(h, now=1300.0) is True   # +300s → allowed
    assert cam._claim_auto_vision_slot(h, now=1400.0) is False  # window restarts


def test_non_numeric_interval_reads_as_off(cam, monkeypatch):
    _set_interval(cam, monkeypatch, "not-a-number")
    assert cam._auto_vision_interval(object()) == 0.0
    assert cam._claim_auto_vision_slot(object(), now=0.0) is True


# ── the short-circuit in async_analyze_camera ────────────────────────────────

class _Call:
    def __init__(self, data):
        self.data = data


@pytest.mark.asyncio
async def test_throttled_auto_analysis_skips_before_any_capture(cam, monkeypatch):
    # A throttled discretionary call must return "throttled" without capturing a
    # frame or touching the vision model — that is the whole point (no tokens).
    monkeypatch.setattr(cam, "_camera_friendly_name", lambda h, e: "Hallway")
    monkeypatch.setattr(cam, "_camera_integration", lambda h, e: "generic")
    monkeypatch.setattr(cam, "_claim_auto_vision_slot", lambda h, now=None: False)

    async def _boom(*a, **k):
        raise AssertionError("capture must not happen when throttled")
    monkeypatch.setattr(cam, "_get_best_image", _boom)
    monkeypatch.setattr(cam, "_capture_frame_sequence", _boom)

    res = await cam.async_analyze_camera(
        object(), _Call({"entity_id": "camera.hallway"}), None, "sir", None, [],
        throttle=True)
    assert res == {"success": False, "error": "throttled", "camera": "Hallway"}


@pytest.mark.asyncio
async def test_explicit_call_is_never_throttled(cam, monkeypatch):
    # throttle defaults False (explicit service / agent / doorbell / person-
    # confirm): the slot limiter must not even be consulted, so an explicit
    # request is never dropped no matter how recently vision ran.
    monkeypatch.setattr(cam, "_camera_friendly_name", lambda h, e: "Hallway")
    monkeypatch.setattr(cam, "_camera_integration", lambda h, e: "generic")

    def _must_not_claim(*a, **k):
        raise AssertionError("explicit call must not consult the throttle")
    monkeypatch.setattr(cam, "_claim_auto_vision_slot", _must_not_claim)

    captured = {}

    async def _no_image(hass, entity_id):
        captured["reached_capture"] = True
        return None   # no image → function returns its no_image result, past the gate
    monkeypatch.setattr(cam, "_get_best_image", _no_image)

    res = await cam.async_analyze_camera(
        object(), _Call({"entity_id": "camera.hallway", "announce": False}),
        None, "sir", None, [])
    # It got past the (unconsulted) throttle gate to real work.
    assert captured.get("reached_capture") is True
    assert res.get("error") != "throttled"


# ── the auto-event wrapper opts into throttling ──────────────────────────────

@pytest.mark.asyncio
async def test_auto_event_wrapper_requests_throttling(cam, monkeypatch):
    seen = {}

    async def _fake_analyze(hass, call, client, honorific, tts, speakers, **kw):
        seen["throttle"] = kw.get("throttle")
        return {"success": True}

    async def _no_sleep(*a, **k):
        return None
    monkeypatch.setattr(cam, "async_analyze_camera", _fake_analyze)
    monkeypatch.setattr(cam.asyncio, "sleep", _no_sleep)

    await cam.async_auto_analyze_on_event(
        hass=object(), groq_client=None, honorific="sir", tts_entity=None,
        speakers=[], entity_id="camera.hallway", reason="Motion detected",
        announce=False)
    assert seen.get("throttle") is True
