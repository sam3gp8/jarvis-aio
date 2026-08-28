"""Tests for continued conversation / turn-taking (continued_conversation, v6.88.0)."""
import pytest


@pytest.fixture
def cc(load):
    return load("continued_conversation")


def test_continue_on_question(cc):
    assert cc.should_continue("Which room did you mean?") is True


def test_no_continue_on_statement(cc):
    assert cc.should_continue("Done. The kitchen light is on.") is False


def test_no_continue_on_empty(cc):
    assert cc.should_continue("") is False
    assert cc.should_continue("   ") is False


def test_continue_on_offer(cc):
    assert cc.should_continue("I can set that up — shall I?") is True
    assert cc.should_continue("Would you like me to schedule it.") is True
    assert cc.should_continue("Let me know how you'd like to proceed.") is True


def test_no_continue_on_midtext_question_mark(cc):
    assert cc.should_continue("You asked why? Here is the reason: it was off.") is False


def test_enabled_default_off(cc, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get", lambda k, d=None: d)          # nothing set
    assert cc.enabled() is False


def test_enabled_when_set(cc, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get",
                        lambda k, d=None: True if k == "continued_conversation_enabled" else d)
    assert cc.enabled() is True


# ── speaker-aware follow-up reopen ──────────────────────────────────────────
import asyncio


def test_speech_seconds_bounds(cc):
    assert cc._speech_seconds("") == 1.5              # floor
    assert cc._speech_seconds("x" * 100000) == 20.0   # cap
    assert 1.5 <= cc._speech_seconds("Which room would you like?") <= 20.0


def test_speaker_reopen_default_on(cc, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get", lambda k, d=None: d)   # nothing configured
    assert cc.speaker_reopen_enabled() is True


def test_schedule_reopen_requires_both_ids(cc, fake_hass):
    assert cc.schedule_reopen(fake_hass, "", "media_player.k", "hi") is False
    assert cc.schedule_reopen(fake_hass, "assist_satellite.b", "", "hi") is False
    assert cc.schedule_reopen(fake_hass, "assist_satellite.b", "media_player.k", "hi") is True
    fake_hass.close_pending()   # don't run the coro; only checking scheduling


@pytest.mark.asyncio
async def test_reopen_waits_for_speaker_then_reopens(cc, fake_hass, monkeypatch):
    sat, spk = "assist_satellite.box", "media_player.kitchen"
    fake_hass.states.set(spk, "playing")     # reply is playing on the speaker
    calls = [0]

    async def fake_sleep(_):
        calls[0] += 1
        if calls[0] == 3:                    # speaker finishes mid-wait
            fake_hass.states.set(spk, "idle")

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await cc._reopen_after_speaker(fake_hass, sat, spk, "Which room?")

    last = fake_hass.service_calls[-1]
    assert (last[0], last[1]) == ("assist_satellite", "start_conversation")
    assert last[2]["entity_id"] == sat        # mic reopened on the satellite


@pytest.mark.asyncio
async def test_reopen_falls_back_when_speaker_never_plays(cc, fake_hass, monkeypatch):
    sat, spk = "assist_satellite.box", "media_player.group"
    fake_hass.states.set(spk, "idle")        # never reports 'playing' (cast group)

    async def fake_sleep(_):
        pass

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await cc._reopen_after_speaker(fake_hass, sat, spk, "Which room?")
    # still reopened, via the spoken-length fallback
    assert any(c[1] == "start_conversation" for c in fake_hass.service_calls)
