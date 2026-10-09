"""Phase I4 — the boot-continuity announce seam (continuity.announce_resume).

Default OFF must be completely silent (behaviour-preserving); when the owner
flips CONTINUITY_RESUME_ANNOUNCE on, it speaks resume_summary once through the
output seam. The TTS/speaker helpers are lazily imported inside the function, so
the enabled-path test injects lightweight stubs under the jc namespace.
"""
import sys
import types

import pytest


@pytest.fixture
def cont(load):
    return load("continuity")


async def test_default_off_is_silent(cont):
    # Default (CONTINUITY_RESUME_ANNOUNCE False) → returns False, speaks nothing.
    assert cont.CONTINUITY_RESUME_ANNOUNCE is False
    assert await cont.announce_resume(hass=object(), entry=None) is False


async def test_enabled_but_empty_summary_says_nothing(cont, monkeypatch):
    monkeypatch.setattr(cont, "CONTINUITY_RESUME_ANNOUNCE", True)
    monkeypatch.setattr(cont, "resume_summary", lambda hass=None: "")
    assert await cont.announce_resume(hass=object(), entry=None) is False


async def test_enabled_speaks_resume_line_through_seam(cont, monkeypatch):
    monkeypatch.setattr(cont, "CONTINUITY_RESUME_ANNOUNCE", True)
    monkeypatch.setattr(cont, "resume_summary",
                        lambda hass=None: "I was arming the alarm.")

    # jarvis_config.runtime_get → return configured TTS + speakers.
    jc_cfg = load_module(cont, "jarvis_config")
    monkeypatch.setattr(jc_cfg, "runtime_get",
                        lambda hass, entry, key, default=None: {
                            "tts_engine": "tts.piper",
                            "broadcast_group": "",
                            "announcement_speakers": ["media_player.kitchen"],
                        }.get(key, default))

    spoken = {}

    async def _async_announce(hass, text, tts_entity, speakers, use_announce=True,
                              context="chat"):
        spoken["text"] = text
        spoken["tts"] = tts_entity
        spoken["speakers"] = list(speakers)
        return True

    tts_stub = types.ModuleType("jc.tts_helper")
    tts_stub.resolve_tts_entity = lambda hass, configured: "tts.piper"
    tts_stub.async_announce = _async_announce
    ar_stub = types.ModuleType("jc.audio_routing")
    ar_stub.broadcast_target = lambda hass, broadcast_group=None, \
        announcement_speakers=None: list(announcement_speakers or [])
    monkeypatch.setitem(sys.modules, "jc.tts_helper", tts_stub)
    monkeypatch.setitem(sys.modules, "jc.audio_routing", ar_stub)

    ok = await cont.announce_resume(hass=object(), entry=object())
    assert ok is True
    assert spoken["text"] == "I was arming the alarm."
    assert spoken["tts"] == "tts.piper"
    assert spoken["speakers"] == ["media_player.kitchen"]


async def test_enabled_no_speakers_is_safe(cont, monkeypatch):
    monkeypatch.setattr(cont, "CONTINUITY_RESUME_ANNOUNCE", True)
    monkeypatch.setattr(cont, "resume_summary", lambda hass=None: "something")
    jc_cfg = load_module(cont, "jarvis_config")
    monkeypatch.setattr(jc_cfg, "runtime_get",
                        lambda hass, entry, key, default=None: default)

    tts_stub = types.ModuleType("jc.tts_helper")
    tts_stub.resolve_tts_entity = lambda hass, configured: None   # no TTS
    tts_stub.async_announce = None  # must not be called
    ar_stub = types.ModuleType("jc.audio_routing")
    ar_stub.broadcast_target = lambda hass, **k: []               # no speakers
    monkeypatch.setitem(sys.modules, "jc.tts_helper", tts_stub)
    monkeypatch.setitem(sys.modules, "jc.audio_routing", ar_stub)

    assert await cont.announce_resume(hass=object(), entry=object()) is False


def load_module(cont, name):
    """Import jc.<name> the way continuity's lazy imports resolve it."""
    import importlib
    return importlib.import_module(f"jc.{name}")
