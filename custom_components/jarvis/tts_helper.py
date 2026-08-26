"""
JARVIS — shared TTS announcement helper.

Supports **hybrid TTS routing**: use a cheap local voice for routine chat,
and a premium voice (e.g. ElevenLabs) for "cinematic" moments like briefings,
doorbell announcements, or sentinel alerts.

Configuration flow:
  - CONF_TTS_ENGINE (default tts_helper auto-pick): used for all routine replies
  - CONF_TTS_PREMIUM_ENGINE (optional): used for high-impact contexts
  - CONF_TTS_PREMIUM_CONTEXTS (list): which contexts route to premium, e.g.
    ["briefing", "doorbell", "camera", "sentinel"]

When a context isn't in the premium list, we use the regular TTS engine.
When the premium engine isn't set at all, everything uses regular.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Context labels — passed by each calling service to tell us what it is.
# Services pass one of these strings via the 'context' parameter.
KNOWN_CONTEXTS = {
    "chat",        # normal conversation
    "briefing",    # morning / on-demand briefing
    "camera",      # camera analysis (doorbell included)
    "doorbell",    # explicitly a doorbell event
    "sentinel",    # proactive alerts
    "reminder",    # reminder announcements
    "routine",     # routine narration (goodnight, goodmorning, etc.)
    "recognition", # face recognition announcement
    "summary",     # conversation summary
    "appliance",   # appliance cycle complete (v5.7.00)
    "reply",       # direct conversation reply (v5.7.00)
}


# ─── TTS entity discovery ────────────────────────────────────────────────────

def find_best_tts_entity(hass: HomeAssistant) -> str | None:
    """
    Auto-discover the best free/local TTS entity.
    Priority: piper > edge_tts > any tts.*
    """
    states = hass.states.async_all("tts")
    for state in states:
        if "piper" in state.entity_id.lower():
            return state.entity_id
    for state in states:
        if "edge" in state.entity_id.lower():
            return state.entity_id
    if states:
        return states[0].entity_id
    return None


def find_premium_tts_entity(hass: HomeAssistant) -> str | None:
    """
    Auto-discover a premium TTS entity.
    Priority: elevenlabs > openai > azure > any non-local.
    Explicitly EXCLUDES home_assistant_cloud — that's Nabu Casa's basic
    TTS, not premium quality. It also doesn't support Piper voice options.
    """
    states = hass.states.async_all("tts")
    for preferred in ("elevenlabs", "eleven_labs", "openai", "azure"):
        for state in states:
            if preferred in state.entity_id.lower():
                return state.entity_id
    return None


# Backward compat alias
find_piper_entity = find_best_tts_entity


def resolve_tts_entity(hass: HomeAssistant, configured: str) -> str | None:
    """Resolve the regular TTS entity. Preserved for backward compat."""
    if configured and configured != "auto":
        if hass.states.get(configured):
            return configured
        _LOGGER.warning("JARVIS: TTS entity '%s' not found — falling back to auto", configured)
    found = find_best_tts_entity(hass)
    if found:
        return found
    _LOGGER.debug("JARVIS: no TTS entity found — broadcast disabled")
    return None


def resolve_tts_for_context(
    hass: HomeAssistant,
    context: str,
    regular_configured: str,
    premium_configured: Optional[str] = None,
    premium_contexts: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """
    Pick the right TTS entity for the given context.

    If context is in premium_contexts AND a premium engine is available,
    return the premium engine. Otherwise return the regular engine.

    premium_contexts is sanitized here — unknown strings are dropped and
    an empty list falls through to regular TTS for everything.

    This means a user can set:
      premium_engine = "tts.elevenlabs"
      premium_contexts = ["briefing", "doorbell", "camera", "recognition"]

    And then:
      - Normal chat replies use Piper (free)
      - Morning briefing uses ElevenLabs (cinematic)
      - Doorbell announcements use ElevenLabs (important)
      - Camera analysis uses ElevenLabs (important)
      - Sentinel "door left open" uses Piper (routine)
      - Face recognition uses ElevenLabs (impressive)
    """
    # Sanitize premium_contexts: keep only recognised context names so a
    # typo in the addon UI doesn't silently break routing.
    cleaned = set()
    for c in (premium_contexts or []):
        c = (c or "").strip().lower()
        if c in KNOWN_CONTEXTS:
            cleaned.add(c)
        elif c:
            _LOGGER.debug(
                "JARVIS TTS: unknown premium context '%s' ignored", c
            )
    premium_contexts = cleaned
    premium_contexts = set(premium_contexts or [])

    # If this context qualifies for premium treatment...
    if context in premium_contexts:
        # Try the explicitly configured premium engine first
        if premium_configured and premium_configured != "auto":
            if hass.states.get(premium_configured):
                _LOGGER.debug("JARVIS: context '%s' → premium TTS %s",
                              context, premium_configured)
                return premium_configured
            _LOGGER.debug(
                "JARVIS: configured premium TTS '%s' not found — trying auto",
                premium_configured,
            )
        # Fall through to auto-discover
        found = find_premium_tts_entity(hass)
        if found:
            _LOGGER.debug("JARVIS: context '%s' → auto-picked premium TTS %s",
                          context, found)
            return found
        # No premium TTS available — fall through to regular (no warning log,
        # this is expected when running free-only)

    # Regular path — the normal TTS engine
    return resolve_tts_entity(hass, regular_configured)


# ─── The announce primitive ──────────────────────────────────────────────────

async def async_announce(
    hass: HomeAssistant,
    text: str,
    tts_entity: str | None,
    speakers: Sequence[str],
    use_announce: bool = True,
    context: str = "chat",
) -> bool:
    """
    Speak text via tts_entity to the given speaker list. No-op if either empty.

    Returns True if the reply was handed to at least one speaker, False if it
    could not be delivered at all — callers that silence a satellite when they
    route a reply here rely on this so a delivery failure doesn't turn into
    total silence.

    Delivery (v5.9.12): uses the `tts.speak` service, which renders through the
    named TTS entity and plays to the given media_players. This is the delivery
    that reliably produces audio across Cast, Nest, Sonos and Wyoming-satellite
    media_players.

    (v5.9.11 attempted `media_player.play_media` with a media-source URL to force
    the voice. That path succeeds *silently* on some targets — notably Cast
    groups, which don't honor the `announce` flag — returning no error while
    producing no sound. Because the conversation layer silences the satellite
    whenever it routes a reply here, that silent success meant no audio at all.
    Reverted to tts.speak, which is proven to play on these targets.)

    The jarvis voice is requested via the `voice` option. We deliberately do NOT
    send a `language` field alongside it: with some Piper/Wyoming builds, a
    language hint makes the engine fall back to a language-default voice instead
    of honoring the explicit `voice`. The voice string already encodes its
    language (en_GB), so Piper infers it correctly.

    `context` is accepted for logging; callers resolve the entity beforehand.
    """
    if not text or not tts_entity or not speakers:
        return False

    _LOGGER.debug("JARVIS announce [%s]: %s → %s", context, tts_entity, speakers)

    is_piper = "piper" in tts_entity.lower()

    service_data = {
        "media_player_entity_id": list(speakers),
        "message": text,
        "cache": True,
    }
    if is_piper:
        # Request the JARVIS Piper voice. No `language`/`length_scale` keys: this
        # Piper build rejects length_scale ("Invalid options found") before any
        # audio plays. If the voice isn't installed (VoiceNotFoundError), the
        # per-speaker fallback below retries without it and uses the engine's
        # default voice, so a missing custom voice is never fatal.
        service_data["options"] = {
            "voice": "en_GB-jarvis-high",
        }

    try:
        await hass.services.async_call(
            "tts", "speak", service_data,
            target={"entity_id": tts_entity}, blocking=False,
        )
        try:
            from .diagnostics.service_health import record_usage
            record_usage("tts", True)
        except Exception:
            pass
        return True
    except Exception as exc:
        # One bad target must not silence everyone (v6.78.2). A single
        # tts.speak carrying the whole speaker list fails as a unit, so a
        # broadcast to every media_player in the house died entirely if one
        # of them (an off TV, a stale Cast entity) rejected the call — while
        # a room-routed reply to a single speaker worked fine. Retry
        # per-speaker so the reachable ones still hear it.
        #
        # And crucially, retry each speaker WITHOUT the voice options as a last
        # resort: a custom Piper voice removed or renamed by a Piper update
        # (e.g. en_GB-jarvis-high) makes tts.speak reject the call, and because
        # the conversation layer silences the satellite whenever it routes a
        # reply here, that rejection meant total silence. Falling back to the
        # engine's default voice keeps the reply audible.
        _LOGGER.warning("JARVIS TTS batch failed (%s): %s — retrying per speaker",
                        context, exc)
        base_opts = service_data.get("options")
        opt_variants = [base_opts, None] if base_opts is not None else [None]
        delivered, failed = 0, []
        for spk in list(speakers):
            spk_ok = False
            last_err = f"{spk}: unknown"
            for opts in opt_variants:
                try:
                    one = {
                        "media_player_entity_id": [spk],
                        "message": text,
                        "cache": True,
                    }
                    if opts:
                        one["options"] = opts
                    await hass.services.async_call(
                        "tts", "speak", one,
                        target={"entity_id": tts_entity}, blocking=False,
                    )
                    delivered += 1
                    spk_ok = True
                    if opts is None and base_opts is not None:
                        _LOGGER.info(
                            "JARVIS TTS: voice option failed on %s — used the "
                            "engine's default voice so the reply is still heard", spk)
                    break
                except Exception as sub:
                    last_err = f"{spk}: {str(sub)[:60]}"
            if not spk_ok:
                failed.append(last_err)
        try:
            from .diagnostics.service_health import record_usage
            record_usage("tts", delivered > 0,
                         None if delivered else str(exc)[:120])
        except Exception:
            pass
        if delivered:
            _LOGGER.info("JARVIS TTS delivered to %d/%d speaker(s); failed: %s",
                         delivered, len(list(speakers)), failed or "none")
        else:
            _LOGGER.warning("JARVIS TTS failed on every speaker (%s): %s",
                            context, failed)
        return delivered > 0
