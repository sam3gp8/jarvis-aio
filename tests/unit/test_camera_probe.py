"""Tests for camera.probe_camera (v6.46.2) — the diagnostic mirror of
_get_best_image built to end the blind debugging of blank Nest tiles."""
import types

import pytest


@pytest.fixture
def cam(load):
    return load("camera")


def _img(content: bytes):
    return types.SimpleNamespace(content=content)


GOOD = b"\xff\xd8" + b"\x7f" * 20_000   # > SMALL_SUSPECT_SIZE — a real frame
TINY = b"\xff\xd8" + b"\x7f" * 2_500    # placeholder-sized, non-blank


async def test_probe_missing_entity(cam, fake_hass):
    out = await cam.probe_camera(fake_hass, "camera.ghost")
    assert out["available"] is False
    assert "missing" in out["verdict"]
    assert out["tiers"] == []


async def test_probe_unavailable_entity(cam, fake_hass):
    fake_hass.states.set("camera.dead", "unavailable")
    out = await cam.probe_camera(fake_hass, "camera.dead")
    assert out["available"] is False
    assert "UNAVAILABLE" in out["verdict"]


async def test_probe_good_snapshot(cam, fake_hass, monkeypatch):
    fake_hass.states.set("camera.ok", "idle")
    monkeypatch.setattr(cam, "find_backend", lambda h, e: None)
    monkeypatch.setattr(cam, "_looks_blank", lambda b: False)

    async def _get(hass, eid, timeout=10):
        return _img(GOOD)
    monkeypatch.setattr(cam, "camera_get_image", _get)

    out = await cam.probe_camera(fake_hass, "camera.ok")
    assert out["verdict"] == "frames available via standard snapshot"
    assert any(t[0] == "snapshot" and t[1].startswith("OK") for t in out["tiers"])
    assert out["elapsed_ms"] >= 0


async def test_probe_backend_ok_short_circuits(cam, fake_hass, monkeypatch):
    fake_hass.states.set("camera.f", "idle")

    class FakeBackend:
        name = "frigate"
        async def fetch_best_image(self, hass, eid, cache):
            return GOOD
    monkeypatch.setattr(cam, "find_backend", lambda h, e: FakeBackend())

    out = await cam.probe_camera(fake_hass, "camera.f")
    assert out["verdict"] == "frames available via frigate backend"
    assert len(out["tiers"]) == 1   # never reached snapshot tiers


async def test_probe_nest_total_failure_gives_actionable_verdict(cam, fake_hass, monkeypatch):
    fake_hass.states.set("camera.nest_door", "idle")

    class NestLike:
        name = "nest"
        async def fetch_best_image(self, hass, eid, cache):
            return None
    monkeypatch.setattr(cam, "find_backend", lambda h, e: NestLike())
    monkeypatch.setattr(
        cam, "er", types.SimpleNamespace(async_get=lambda h: types.SimpleNamespace(
            async_get=lambda eid: types.SimpleNamespace(platform="nest"))))

    async def _boom(hass, eid, timeout=10):
        raise RuntimeError("stream not ready")
    monkeypatch.setattr(cam, "camera_get_image", _boom)

    async def _warm(hass, eid, settle=2.5):
        return False
    monkeypatch.setattr(cam, "_prewarm_stream", _warm)

    out = await cam.probe_camera(fake_hass, "camera.nest_door")
    assert out["platform"] == "nest"
    tiers = dict(out["tiers"])
    assert "no recent event media" in tiers["backend:nest"]
    assert "RuntimeError" in tiers["snapshot"]
    assert "Pub/Sub" in out["verdict"]          # actionable nest guidance
    assert "event" in out["verdict"]


async def test_probe_blank_then_wake_recovers(cam, fake_hass, monkeypatch):
    fake_hass.states.set("camera.wake", "idle")
    monkeypatch.setattr(cam, "find_backend", lambda h, e: None)

    calls = {"n": 0}
    async def _get(hass, eid, timeout=10):
        calls["n"] += 1
        return _img(b"\x00" * 3000 if calls["n"] == 1 else GOOD)
    monkeypatch.setattr(cam, "camera_get_image", _get)
    monkeypatch.setattr(cam, "_looks_blank", lambda b: b[0:1] == b"\x00")

    woke = {"v": False}
    async def _warm(hass, eid, settle=2.5):
        woke["v"] = True
        return True
    monkeypatch.setattr(cam, "_prewarm_stream", _warm)

    out = await cam.probe_camera(fake_hass, "camera.wake")
    assert woke["v"] is True
    assert out["verdict"] == "frames available after stream wake (slow path)"
    tiers = dict(out["tiers"])
    assert "BLANK" in tiers["snapshot"]
    assert tiers["wake-retry"].startswith("OK")


# ── v6.46.3: tiny "successful" frames are placeholders, not victories ────────

async def test_probe_tiny_frame_is_suspect_then_wake_recovers(cam, fake_hass, monkeypatch):
    fake_hass.states.set("camera.tiny", "streaming")
    monkeypatch.setattr(cam, "find_backend", lambda h, e: None)
    monkeypatch.setattr(cam, "_looks_blank", lambda b: False)

    calls = {"n": 0}
    async def _get(hass, eid, timeout=10):
        calls["n"] += 1
        return _img(TINY if calls["n"] == 1 else GOOD)
    monkeypatch.setattr(cam, "camera_get_image", _get)

    async def _warm(hass, eid, settle=2.5):
        return True
    monkeypatch.setattr(cam, "_prewarm_stream", _warm)

    out = await cam.probe_camera(fake_hass, "camera.tiny")
    tiers = dict(out["tiers"])
    assert "SUSPECT" in tiers["snapshot"]           # not declared a win
    assert tiers["wake-retry"].startswith("OK")
    assert out["verdict"] == "frames available after stream wake (slow path)"


async def test_probe_tiny_frame_wake_no_better_placeholder_verdict(cam, fake_hass, monkeypatch):
    fake_hass.states.set("camera.tiny2", "streaming")
    monkeypatch.setattr(cam, "find_backend", lambda h, e: None)
    monkeypatch.setattr(cam, "_looks_blank", lambda b: False)

    async def _get(hass, eid, timeout=10):
        return _img(TINY)
    monkeypatch.setattr(cam, "camera_get_image", _get)

    async def _warm(hass, eid, settle=2.5):
        return True
    monkeypatch.setattr(cam, "_prewarm_stream", _warm)

    out = await cam.probe_camera(fake_hass, "camera.tiny2")
    assert "placeholder-sized" in out["verdict"]


async def test_get_best_image_wakes_on_tiny_and_prefers_real_frame(cam, fake_hass, monkeypatch):
    monkeypatch.setattr(cam, "find_backend", lambda h, e: None)
    monkeypatch.setattr(cam, "_looks_blank", lambda b: False)

    calls = {"n": 0}
    async def _get(hass, eid, timeout=10):
        calls["n"] += 1
        return _img(TINY if calls["n"] == 1 else GOOD)
    monkeypatch.setattr(cam, "camera_get_image", _get)

    woke = {"v": False}
    async def _warm(hass, eid, settle=2.5):
        woke["v"] = True
        return True
    monkeypatch.setattr(cam, "_prewarm_stream", _warm)

    out = await cam._get_best_image(fake_hass, "camera.tiny3")
    assert woke["v"] is True          # tiny first frame forced the wake
    assert out == GOOD                # and the real frame won


async def test_get_best_image_keeps_tiny_as_last_resort(cam, fake_hass, monkeypatch):
    monkeypatch.setattr(cam, "find_backend", lambda h, e: None)
    monkeypatch.setattr(cam, "_looks_blank", lambda b: False)

    async def _get(hass, eid, timeout=10):
        return _img(TINY)
    monkeypatch.setattr(cam, "camera_get_image", _get)

    async def _warm(hass, eid, settle=2.5):
        return True
    monkeypatch.setattr(cam, "_prewarm_stream", _warm)

    out = await cam._get_best_image(fake_hass, "camera.tiny4")
    assert out == TINY                # tiny beats None


# ── v6.47.0: camera_overrides — restream twins take over frame duty ─────────

def _set_overrides(load, monkeypatch, mapping):
    jcfg = load("jarvis_config")
    monkeypatch.setattr(jcfg, "get",
                        lambda key, default=None: mapping if key == "camera_overrides" else default)


async def test_override_redirects_frame_fetch(cam, fake_hass, load, monkeypatch):
    fake_hass.states.set("camera.nest_orig", "streaming")
    fake_hass.states.set("camera.restream_twin", "streaming")
    _set_overrides(load, monkeypatch, {"camera.nest_orig": "camera.restream_twin"})
    monkeypatch.setattr(cam, "find_backend", lambda h, e: None)
    monkeypatch.setattr(cam, "_looks_blank", lambda b: False)

    seen = []
    async def _get(hass, eid, timeout=10):
        seen.append(eid)
        return _img(GOOD if eid == "camera.restream_twin" else TINY)
    monkeypatch.setattr(cam, "camera_get_image", _get)

    out = await cam._get_best_image(fake_hass, "camera.nest_orig")
    assert out == GOOD
    assert seen == ["camera.restream_twin"]   # never touched the nest path


async def test_override_missing_target_falls_back(cam, fake_hass, load, monkeypatch):
    fake_hass.states.set("camera.nest_orig2", "streaming")
    _set_overrides(load, monkeypatch, {"camera.nest_orig2": "camera.ghost_twin"})
    assert cam.resolve_camera_source(fake_hass, "camera.nest_orig2") == "camera.nest_orig2"


async def test_probe_reports_override(cam, fake_hass, load, monkeypatch):
    fake_hass.states.set("camera.nest_orig3", "streaming")
    fake_hass.states.set("camera.twin3", "streaming")
    _set_overrides(load, monkeypatch, {"camera.nest_orig3": "camera.twin3"})
    monkeypatch.setattr(cam, "find_backend", lambda h, e: None)
    monkeypatch.setattr(cam, "_looks_blank", lambda b: False)

    async def _get(hass, eid, timeout=10):
        return _img(GOOD)
    monkeypatch.setattr(cam, "camera_get_image", _get)

    out = await cam.probe_camera(fake_hass, "camera.nest_orig3")
    assert out["override"] == "camera.twin3"
    tiers = dict(out["tiers"])
    assert "camera.twin3" in tiers["override"]
    assert out["verdict"] == "frames available via standard snapshot"
