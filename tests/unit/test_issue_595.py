"""Issue #595 — "Say Something" (relay) and the notify queue announced nothing.

``relay.async_relay`` and ``notify_queue._announce`` called
``audio_routing.broadcast_target`` with NO arguments (so the configured
``announcement_speakers`` were never seen) and ``async_announce`` with
``tts_entity=None`` (which returns False immediately, line 357). Both ignored
that: relay returned ``{"success": True, "spoken": ...}`` and the queue returned
True — a "spoken" toast with no audio.

The behavioural guards for relay/notify live in test_relay.py and
test_notify_queue.py (both now assert failure, not false success, when a speaker
/ TTS engine is missing or delivery returns False). This file covers the shared
resolver that replaced the no-arg call and the newly-registered context.
"""
from __future__ import annotations

import types

import pytest


def test_announcement_registered_as_known_context(load):
    # So an announcement can be premium-TTS routed like any other context, and
    # the live context string relay/notify pass is a first-class member.
    tts = load("tts_helper")
    assert "announcement" in tts.KNOWN_CONTEXTS


def test_announcement_targets_resolves_panel_speakers_and_drops_satellites(load, fake_hass):
    ar = load("audio_routing")
    load("jarvis_config")
    load("tts_helper")
    DOMAIN = load("const").DOMAIN

    fake_hass.states.set("tts.piper", "idle")             # a real engine for auto
    fake_hass.states.set("media_player.kitchen", "idle")
    fake_hass.states.set("assist_satellite.office", "idle")
    # panel-live announcement_speakers (written for no-reload changes), including
    # a listen-only satellite that must never be an output target.
    fake_hass.data[DOMAIN] = {"e1": {"runtime_config": {
        "announcement_speakers": ["media_player.kitchen", "assist_satellite.office"]}}}
    # a panel-only install has zero config entries — the resolver must still work.
    fake_hass.config_entries = types.SimpleNamespace(async_entries=lambda d: [])

    speakers, tts_entity = ar.announcement_targets(fake_hass)
    assert speakers == ["media_player.kitchen"]           # satellite dropped
    assert tts_entity == "tts.piper"                      # never None (#595)


def test_announcement_targets_skips_unavailable_speakers(load, fake_hass):
    # A configured speaker whose entity no longer exists is filtered out, so a
    # stale selection can't masquerade as a live target.
    ar = load("audio_routing")
    load("jarvis_config")
    load("tts_helper")
    DOMAIN = load("const").DOMAIN

    fake_hass.states.set("tts.piper", "idle")
    fake_hass.states.set("media_player.kitchen", "idle")
    fake_hass.data[DOMAIN] = {"e1": {"runtime_config": {
        "announcement_speakers": ["media_player.kitchen", "media_player.gone"]}}}
    fake_hass.config_entries = types.SimpleNamespace(async_entries=lambda d: [])

    speakers, tts_entity = ar.announcement_targets(fake_hass)
    assert speakers == ["media_player.kitchen"]
    assert tts_entity == "tts.piper"


def test_announcement_targets_silent_until_configured(load, fake_hass):
    # Nothing configured -> empty speakers (silent, never "all 17 devices") and
    # no TTS entity. The caller turns this into an honest error, not a no-op.
    ar = load("audio_routing")
    load("jarvis_config")
    load("tts_helper")
    fake_hass.config_entries = types.SimpleNamespace(async_entries=lambda d: [])

    speakers, tts_entity = ar.announcement_targets(fake_hass)
    assert speakers == []
    assert tts_entity is None
