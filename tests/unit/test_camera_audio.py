"""Two-way camera audio — talk half (#552).

Covers the go2rtc provider orchestration with the HTTP and TTS-URL layers
mocked: capability parsing (sendonly / recvonly / idle-unknown), stream & group
resolution, the muted-until-requested gate, graceful recvonly refusal, the TTS
requirement, codec preference, push-failure reporting, and the per-NVR
backchannel mutex. Live go2rtc/camera delivery is hardware and can't run here;
these pin the decision logic that decides whether and how a push happens.
"""
import asyncio
import sys
import types

import pytest


@pytest.fixture
def ca(load):
    return load("camera_audio")


def _stub_ws(monkeypatch, config: dict):
    """Stub jc.websocket so camera_audio's _cfg/_cfg_json read `config`."""
    mod = types.ModuleType("jc.websocket")
    mod._runtime_opt = lambda hass, entry, key, default=None: config.get(key, default)
    mod._get_runtime_json = lambda hass, entry, key, default: config.get(key, default)
    monkeypatch.setitem(sys.modules, "jc.websocket", mod)
    pkg = sys.modules.get("jc")
    if pkg is not None:
        monkeypatch.setattr(pkg, "websocket", mod, raising=False)


# ── _parse_capabilities (pure) ───────────────────────────────────────────────

def test_parse_sendonly_is_talkback(ca):
    body = {"Garage": {"producers": [{"medias": ["video, recvonly, H264",
                                                 "audio, sendonly, PCMA/8000"]}]}}
    caps = ca._parse_capabilities("Garage", body)
    assert caps.talkback is True
    assert "pcma" in caps.codecs
    assert caps.online is True


def test_parse_recvonly_is_false(ca):
    # active stream, audio present but only recvonly (listen-only, e.g. Nest)
    body = {"Elianas_Room": {"producers": [{"medias": ["video, recvonly, H264",
                                                       "audio, recvonly, OPUS/48000/2"]}]}}
    caps = ca._parse_capabilities("Elianas_Room", body)
    assert caps.talkback is False
    assert caps.online is True


def test_parse_idle_producers_null_is_unknown(ca):
    # on-demand stream idle: producers null -> UNKNOWN, never "unsupported"
    body = {"Backyard_2": {"producers": None}}
    caps = ca._parse_capabilities("Backyard_2", body)
    assert caps.talkback is None
    assert caps.online is False


def test_parse_missing_stream_is_unknown_offline(ca):
    caps = ca._parse_capabilities("Nope", {"Other": {"producers": []}})
    assert caps.talkback is None
    assert caps.online is False
    assert "not found" in caps.detail


def test_parse_active_no_audio_is_unknown(ca):
    body = {"Cam": {"producers": [{"medias": ["video, recvonly, H264"]}]}}
    caps = ca._parse_capabilities("Cam", body)
    assert caps.talkback is None
    assert caps.online is True


# ── config resolution ────────────────────────────────────────────────────────

def test_stream_defaults_to_short_name(ca, monkeypatch):
    _stub_ws(monkeypatch, {})
    assert ca.stream_for(None, None, "camera.garage") == "garage"


def test_stream_map_override_by_entity_or_name(ca, monkeypatch):
    _stub_ws(monkeypatch, {"camera_backchannel_map": {"camera.garage": "Garage_2"}})
    assert ca.stream_for(None, None, "camera.garage") == "Garage_2"
    _stub_ws(monkeypatch, {"camera_backchannel_map": {"garage": "Garage_2"}})
    assert ca.stream_for(None, None, "camera.garage") == "Garage_2"


def test_group_defaults_to_stream_then_override(ca, monkeypatch):
    _stub_ws(monkeypatch, {})
    assert ca.group_for(None, None, "camera.garage") == "garage"
    _stub_ws(monkeypatch, {"camera_backchannel_groups": {"garage": "lorex_nvr"}})
    assert ca.group_for(None, None, "camera.garage") == "lorex_nvr"


# ── speak_to_camera orchestration ────────────────────────────────────────────

async def _probe(result):
    async def _p(hass, entry, camera_id):
        return result
    return _p


def _caps(ca, **kw):
    kw.setdefault("stream", "garage")
    return ca.CameraAudioCapabilities(**kw)


async def test_speak_gated_off_by_default(ca, monkeypatch):
    _stub_ws(monkeypatch, {})  # camera_audio_enabled defaults False
    res = await ca.speak_to_camera(None, None, "camera.garage", "intruder, leave now")
    assert res["success"] is False
    assert "off" in res["error"] or "muted" in res["error"]


async def test_speak_requires_message(ca, monkeypatch):
    _stub_ws(monkeypatch, {"camera_audio_enabled": True})
    res = await ca.speak_to_camera(None, None, "camera.garage", "   ")
    assert res["success"] is False
    assert "message is required" in res["error"]


async def test_speak_refuses_recvonly(ca, monkeypatch):
    _stub_ws(monkeypatch, {"camera_audio_enabled": True})
    monkeypatch.setattr(ca, "probe_capabilities", await _probe(_caps(ca, talkback=False, online=True)))
    pushed = []
    async def _push(*a, **k): pushed.append(a)
    monkeypatch.setattr(ca, "_push_audio", _push)
    res = await ca.speak_to_camera(None, None, "camera.garage", "hello")
    assert res["success"] is False
    assert "listen-only" in res["error"] or "recvonly" in res["error"]
    assert pushed == []                     # never pushed to a recvonly camera


async def test_speak_requires_tts_engine(ca, monkeypatch):
    _stub_ws(monkeypatch, {"camera_audio_enabled": True})
    monkeypatch.setattr(ca, "probe_capabilities", await _probe(_caps(ca, talkback=True, codecs=["pcma"], online=True)))
    async def _no_tts(hass, entry, msg): return None
    monkeypatch.setattr(ca, "_tts_audio_url", _no_tts)
    res = await ca.speak_to_camera(None, None, "camera.garage", "hello")
    assert res["success"] is False
    assert "TTS" in res["error"]


async def test_speak_success_pushes(ca, monkeypatch):
    _stub_ws(monkeypatch, {"camera_audio_enabled": True})
    monkeypatch.setattr(ca, "probe_capabilities", await _probe(_caps(ca, talkback=True, codecs=["pcma"], online=True)))
    async def _tts(hass, entry, msg): return "http://ha.local/api/tts_proxy/x.mp3"
    monkeypatch.setattr(ca, "_tts_audio_url", _tts)
    captured = {}
    async def _push(hass, entry, stream, src_url, codec):
        captured.update(stream=stream, src_url=src_url, codec=codec)
    monkeypatch.setattr(ca, "_push_audio", _push)
    res = await ca.speak_to_camera(None, None, "camera.garage", "package delivered")
    assert res["success"] is True
    assert captured["stream"] == "garage"
    assert captured["src_url"].endswith("x.mp3")
    assert captured["codec"] == "pcma"


async def test_speak_prefers_advertised_codec(ca, monkeypatch):
    _stub_ws(monkeypatch, {"camera_audio_enabled": True})
    monkeypatch.setattr(ca, "probe_capabilities", await _probe(_caps(ca, talkback=True, codecs=["pcmu"], online=True)))
    async def _tts(hass, entry, msg): return "http://ha/x.mp3"
    monkeypatch.setattr(ca, "_tts_audio_url", _tts)
    got = {}
    async def _push(hass, entry, stream, src_url, codec): got["codec"] = codec
    monkeypatch.setattr(ca, "_push_audio", _push)
    res = await ca.speak_to_camera(None, None, "camera.garage", "hi")   # default pcma
    assert res["success"] is True
    assert got["codec"] == "pcmu"            # switched to the camera's advertised codec


async def test_speak_allows_unknown_idle_stream(ca, monkeypatch):
    # talkback None (idle/on-demand) must NOT be treated as unsupported.
    _stub_ws(monkeypatch, {"camera_audio_enabled": True})
    monkeypatch.setattr(ca, "probe_capabilities", await _probe(_caps(ca, talkback=None, online=False)))
    async def _tts(hass, entry, msg): return "http://ha/x.mp3"
    monkeypatch.setattr(ca, "_tts_audio_url", _tts)
    async def _push(*a, **k): pass
    monkeypatch.setattr(ca, "_push_audio", _push)
    res = await ca.speak_to_camera(None, None, "camera.garage", "hi")
    assert res["success"] is True


async def test_speak_push_failure_reported(ca, monkeypatch):
    _stub_ws(monkeypatch, {"camera_audio_enabled": True})
    monkeypatch.setattr(ca, "probe_capabilities", await _probe(_caps(ca, talkback=True, codecs=["pcma"], online=True)))
    async def _tts(hass, entry, msg): return "http://ha/x.mp3"
    monkeypatch.setattr(ca, "_tts_audio_url", _tts)
    async def _push(*a, **k): raise RuntimeError("go2rtc push HTTP 500")
    monkeypatch.setattr(ca, "_push_audio", _push)
    res = await ca.speak_to_camera(None, None, "camera.garage", "hi")
    assert res["success"] is False
    assert "push failed" in res["error"]


async def test_tts_url_none_without_engine(ca, monkeypatch):
    # No TTS engine available -> _tts_audio_url returns None so speak_to_camera
    # reports honestly instead of pushing silence.
    _stub_ws(monkeypatch, {})
    th = types.ModuleType("jc.tts_helper")
    th.resolve_tts_entity = lambda hass, cfg: None
    th.find_best_tts_entity = lambda hass: None
    monkeypatch.setitem(sys.modules, "jc.tts_helper", th)
    pkg = sys.modules.get("jc")
    if pkg is not None:
        monkeypatch.setattr(pkg, "tts_helper", th, raising=False)
    assert await ca._tts_audio_url(None, None, "hello") is None


# ── per-NVR backchannel mutex ────────────────────────────────────────────────

async def _run_two(ca, monkeypatch, cam_a, cam_b, config):
    """Run two concurrent speaks; _push records enter/exit so we can see overlap."""
    _stub_ws(monkeypatch, config)
    ca._GROUP_LOCKS.clear()
    monkeypatch.setattr(ca, "probe_capabilities", await _probe(_caps(ca, talkback=True, codecs=["pcma"], online=True)))
    async def _tts(hass, entry, msg): return "http://ha/x.mp3"
    monkeypatch.setattr(ca, "_tts_audio_url", _tts)
    timeline = []
    async def _push(hass, entry, stream, src_url, codec):
        timeline.append(("enter", stream))
        await asyncio.sleep(0.05)
        timeline.append(("exit", stream))
    monkeypatch.setattr(ca, "_push_audio", _push)
    await asyncio.gather(
        ca.speak_to_camera(None, None, cam_a, "a"),
        ca.speak_to_camera(None, None, cam_b, "b"),
    )
    return timeline


def _overlapped(timeline):
    """True if any push entered before the previously-entered one exited."""
    depth = 0
    peak = 0
    for kind, _ in timeline:
        if kind == "enter":
            depth += 1
            peak = max(peak, depth)
        else:
            depth -= 1
    return peak > 1


async def test_mutex_serializes_same_group(ca, monkeypatch):
    # Both cameras share one NVR backchannel group -> pushes must NOT overlap.
    cfg = {"camera_audio_enabled": True,
           "camera_backchannel_groups": {"garage": "lorex", "well": "lorex"}}
    tl = await _run_two(ca, monkeypatch, "camera.garage", "camera.well", cfg)
    assert not _overlapped(tl), f"same-group pushes overlapped: {tl}"


async def test_mutex_allows_parallel_across_groups(ca, monkeypatch):
    # Independent streams (default group = stream name) -> may overlap.
    tl = await _run_two(ca, monkeypatch, "camera.garage", "camera.driveway",
                        {"camera_audio_enabled": True})
    assert _overlapped(tl), f"independent pushes should overlap: {tl}"
