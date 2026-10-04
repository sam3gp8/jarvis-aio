"""Notifications-only speaker mute (#181).

When `announce_notify_only` is set, observer_speak_target must route EVERY
proactive announcement — every urgency, including critical — to notify_only, so
JARVIS never speaks on a speaker (text/push instead). Off, routing is unchanged.
"""
import pytest


@pytest.fixture
def ar(load):
    return load("audio_routing")


@pytest.fixture
def jc(load):
    return load("jarvis_config")


def _hass_with_speaker(fake_hass):
    fake_hass.states.set("media_player.office", "idle")
    return fake_hass


def _enable(jc, monkeypatch, on):
    monkeypatch.setattr(jc, "get", lambda k, d=None: (on if k == "announce_notify_only" else d))


@pytest.mark.parametrize("urgency", ["critical", "high", "medium", "low"])
def test_notify_only_forces_push_for_every_urgency(ar, jc, fake_hass, monkeypatch, urgency):
    _enable(jc, monkeypatch, True)
    targets, mode = ar.observer_speak_target(
        _hass_with_speaker(fake_hass),
        urgency=urgency,
        announcement_speakers=["media_player.office"],
        is_sleeping=False,
    )
    assert targets == [] and mode == "notify_only"


def test_off_preserves_normal_broadcast(ar, jc, fake_hass, monkeypatch):
    # With the flag off, a critical announcement still broadcasts to the
    # configured speaker — proving the guard didn't fire.
    _enable(jc, monkeypatch, False)
    targets, mode = ar.observer_speak_target(
        _hass_with_speaker(fake_hass),
        urgency="critical",
        announcement_speakers=["media_player.office"],
        is_sleeping=False,
    )
    assert mode == "broadcast" and targets == ["media_player.office"]
