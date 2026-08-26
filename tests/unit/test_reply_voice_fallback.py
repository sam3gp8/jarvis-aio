"""Reply delivery must survive a broken/missing Piper voice — v7.50.0.

The conversation layer silences the satellite whenever it routes a reply to a
Cast speaker, so if tts.speak is rejected (e.g. a custom Piper voice removed or
renamed by a Piper update) the reply used to vanish entirely. async_announce now
(a) reports whether it delivered, so the caller only silences the satellite on
success, and (b) retries without the voice options — falling back to the
engine's default voice — so a missing custom voice can't cause total silence.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def tts(load):
    return load("tts_helper")


class _OKHass:
    """Accepts every tts.speak call."""
    def __init__(self):
        self.calls = []           # list of (targets, has_options)
        self.services = self
    async def async_call(self, domain, service, data, target=None, blocking=False):
        self.calls.append((list(data.get("media_player_entity_id") or []),
                           "options" in data))


class _VoiceFailHass:
    """Rejects tts.speak when a voice option is present (mimics a missing custom
    voice); accepts it once the options are dropped (default voice)."""
    def __init__(self):
        self.calls = []
        self.services = self
    async def async_call(self, domain, service, data, target=None, blocking=False):
        has_opts = "options" in data
        self.calls.append((list(data.get("media_player_entity_id") or []), has_opts))
        if has_opts:
            raise RuntimeError("Invalid options: voice 'en_GB-jarvis-high' not found")


class _AllFailHass:
    def __init__(self):
        self.services = self
    async def async_call(self, *a, **k):
        raise RuntimeError("tts engine down")


async def test_returns_true_on_success(tts):
    hass = _OKHass()
    ok = await tts.async_announce(hass, "hello", "tts.piper_jarvis",
                                  ["media_player.kitchen"], context="reply")
    assert ok is True
    assert hass.calls and hass.calls[0][1] is True     # sent the piper voice option


async def test_noop_returns_false(tts):
    hass = _OKHass()
    assert await tts.async_announce(hass, "", "tts.piper_jarvis", ["m"]) is False
    assert await tts.async_announce(hass, "hi", None, ["m"]) is False
    assert await tts.async_announce(hass, "hi", "tts.piper_jarvis", []) is False
    assert hass.calls == []                             # never called for a no-op


async def test_missing_voice_falls_back_to_default(tts):
    hass = _VoiceFailHass()
    ok = await tts.async_announce(hass, "hello", "tts.piper_jarvis",
                                  ["media_player.kitchen"], context="reply")
    assert ok is True                                   # delivered via default voice
    # it tried with the voice option (failed) and again without it (succeeded)
    assert any(has for _, has in hass.calls)
    assert any(not has for _, has in hass.calls)


async def test_returns_false_when_all_fail(tts):
    assert await tts.async_announce(_AllFailHass(), "hi", "tts.piper_jarvis",
                                    ["media_player.x"], context="reply") is False


async def test_non_piper_has_no_voice_option(tts):
    hass = _OKHass()
    ok = await tts.async_announce(hass, "hello", "tts.home_assistant_cloud",
                                  ["media_player.kitchen"], context="briefing")
    assert ok is True
    assert hass.calls[0][1] is False                   # no piper voice option for Cloud
