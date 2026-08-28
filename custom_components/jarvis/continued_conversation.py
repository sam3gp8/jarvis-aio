"""
JARVIS — continued conversation / turn-taking (v6.88.0).

Natural turn-taking: after JARVIS asks something, keep the satellite listening
for the reply without a new wake word. This module decides when a response
invites a follow-up (should_continue) and reads the enable flag; conversation.py
sets continue_conversation on the result accordingly, and the assist pipeline
honors it by reopening the mic.

Conservative and OFF by default. It only continues on a clear question or an
explicit offer to act — it will not hold the mic open after a plain statement.

The external-speaker reopen timing IS implemented here (schedule_reopen): for a
mic-only satellite whose reply plays on a separate speaker, JARVIS watches that
speaker and reopens the satellite mic only once it goes idle — so the mic doesn't
capture JARVIS's own reply. The fuller ambient behavior (no-wake response,
barge-in, multi-satellite continuity) is still to come and is validated on the
real satellites.
"""
from __future__ import annotations

import re

_INVITE = re.compile(
    r"(?i)\b(let me know|which (?:one )?would you|shall i|would you like|"
    r"do you want me to|should i|want me to)\b"
)


def enabled() -> bool:
    """Whether continued conversation is turned on (off by default)."""
    try:
        from . import jarvis_config
        return bool(jarvis_config.get("continued_conversation_enabled", False))
    except Exception:
        return False


def should_continue(text: str) -> bool:
    """True when the response invites a reply, so the satellite should keep
    listening without a new wake word. Conservative: a trailing question, or a
    clear offer to act. A plain statement returns False."""
    t = (text or "").strip()
    if not t:
        return False
    if t.endswith("?"):
        return True
    return bool(_INVITE.search(t))


# ── Speaker-aware follow-up reopen ──────────────────────────────────────────
# For a mic-only satellite whose reply plays on a SEPARATE speaker, Home
# Assistant's continue_conversation reopens the mic based on the satellite's own
# (instant) playback — before the speaker has finished — so the mic captures
# JARVIS's own reply. Instead JARVIS watches the reply speaker and reopens the
# satellite mic only once it goes idle. General: any satellite + any speaker.
_PLAYING_STATES = {"playing", "buffering"}
_DONE_STATES = {"idle", "paused", "standby", "off", "unavailable", "unknown"}


def speaker_reopen_enabled() -> bool:
    """Whether JARVIS drives the follow-up reopen itself, timed to the separate
    speaker finishing (default on). Set False to fall back to Home Assistant's
    built-in continue_conversation reopen."""
    try:
        from . import jarvis_config
        return bool(jarvis_config.get("continued_conversation_speaker_reopen", True))
    except Exception:
        return True


def satellite_for_device(hass, device_id: str):
    """The assist_satellite entity_id belonging to `device_id`, or None."""
    if not device_id:
        return None
    try:
        from homeassistant.helpers import entity_registry as er
        reg = er.async_get(hass)
        for ent in reg.entities.values():
            if ent.device_id == device_id and ent.domain == "assist_satellite":
                return ent.entity_id
    except Exception:
        return None
    return None


def _speech_seconds(text: str) -> float:
    """Rough spoken-duration estimate (~12 chars/sec), used as a floor and as the
    fallback when the speaker never reports a 'playing' state (e.g. a Cast group)."""
    return min(20.0, max(1.5, len(text or "") / 12.0 + 0.8))


async def _reopen_after_speaker(hass, satellite_entity_id: str,
                                speaker_entity_id: str, reply_text: str) -> None:
    """Wait for the reply to finish on `speaker_entity_id`, then reopen the
    satellite mic to listen for the follow-up (no wake word, no announcement)."""
    import asyncio
    import time as _t

    est = _speech_seconds(reply_text)
    started = False
    # Phase 1: give the speaker up to ~4s to actually start the announcement.
    for _ in range(16):
        await asyncio.sleep(0.25)
        st = hass.states.get(speaker_entity_id)
        if st is not None and st.state in _PLAYING_STATES:
            started = True
            break
    if started:
        # Phase 2: wait for it to go idle again, capped so we never hang.
        deadline = _t.monotonic() + est + 8.0
        while _t.monotonic() < deadline:
            await asyncio.sleep(0.25)
            st = hass.states.get(speaker_entity_id)
            if st is None or st.state in _DONE_STATES:
                break
        await asyncio.sleep(0.5)   # small settle so the reply tail isn't captured
    else:
        # Speaker never reported playing — fall back to the spoken-length estimate
        # so the mic still reopens at a sane time.
        await asyncio.sleep(est)

    try:
        await hass.services.async_call(
            "assist_satellite",
            "start_conversation",
            {"entity_id": satellite_entity_id, "start_message": ""},
            blocking=False,
        )
    except Exception:
        # If the reopen fails, the turn simply ends — the user can wake normally.
        pass


def schedule_reopen(hass, satellite_entity_id: str,
                    speaker_entity_id: str, reply_text: str) -> bool:
    """Schedule a speaker-aware follow-up mic reopen (non-blocking). Returns True
    if scheduled."""
    if not satellite_entity_id or not speaker_entity_id:
        return False
    hass.async_create_task(
        _reopen_after_speaker(hass, satellite_entity_id, speaker_entity_id, reply_text)
    )
    return True
