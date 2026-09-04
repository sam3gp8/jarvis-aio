"""TTS voice mode — use the JARVIS Piper voice, or Home Assistant's default.

Default keeps the JARVIS voice; with tts_use_ha_voice on (read from
runtime_config), JARVIS omits the `voice` option so the TTS entity uses its
configured default (e.g. a French Piper voice), resolving issue #16.
"""
import pytest

DOMAIN = "jarvis"


@pytest.fixture
def tts(load):
    return load("tts_helper")


def _set_ha_voice(hass, on):
    hass.data.setdefault(DOMAIN, {})["e1"] = {"runtime_config": {"tts_use_ha_voice": on}}


def _speak_call(hass):
    calls = [c for c in hass.service_calls if c[0] == "tts" and c[1] == "speak"]
    assert calls, "expected a tts.speak call"
    return calls[-1][2]


async def test_default_requests_jarvis_voice_on_piper(tts, fake_hass):
    ok = await tts.async_announce(fake_hass, "hello", "tts.piper", ["media_player.x"])
    assert ok is True
    assert _speak_call(fake_hass).get("options", {}).get("voice") == "en_GB-jarvis-high"


async def test_ha_voice_mode_omits_voice(tts, fake_hass):
    _set_ha_voice(fake_hass, True)
    ok = await tts.async_announce(fake_hass, "bonjour", "tts.piper", ["media_player.x"])
    assert ok is True
    data = _speak_call(fake_hass)
    assert "options" not in data or "voice" not in data.get("options", {})


async def test_ha_voice_off_keeps_jarvis_voice(tts, fake_hass):
    _set_ha_voice(fake_hass, False)
    await tts.async_announce(fake_hass, "hi", "tts.piper", ["media_player.x"])
    assert _speak_call(fake_hass).get("options", {}).get("voice") == "en_GB-jarvis-high"


async def test_non_piper_never_forces_voice(tts, fake_hass):
    await tts.async_announce(fake_hass, "hi", "tts.google_ai_tts", ["media_player.x"])
    assert "options" not in _speak_call(fake_hass)
