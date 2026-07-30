"""
JARVIS — Voice confirmation & interactive follow-up (v6.67.0).

Two capabilities, both opt-in via config:

  1. Voice-confirm sensitive actions (lock/unlock, garage, alarm disarm): before
     JARVIS acts on a protected entity, it asks out loud and waits for a spoken
     yes/no — the "unlock the door, you sure?" pattern HA built for exactly this.

  2. Open-ended follow-up: JARVIS asks a question aloud and listens for a free
     answer, feeding it back so a bare "yes" or "the blue one" is understood.

Two delivery paths, chosen by `voice_confirm_mode`:

  - "native"  — uses HA's assist_satellite.ask_question / start_conversation,
     which sequence announce → wait-for-playback → listen internally. This
     "just works" ONLY if the assist_satellite's own audio output routes to a
     real speaker (here, the Nest). JARVIS's satellites are ears-only, so this
     depends on the satellite output being pointed at the Nest at the HA level.

  - "gated"   — the fallback that fits JARVIS's ears/mouth routing: speak the
     question via the normal announce path (to the Nest media_player), wait for
     that media_player to finish playing, then re-open listening on the
     satellite via its ESPHome voice_assistant.start action. No dependency on
     satellite audio output.

  - "auto"    — try native; if the satellite has no usable audio output
     configured, fall back to gated.

Everything here is defensive and never raises to the caller — a failed
confirmation is treated as "not confirmed" (fail safe: the protected action does
NOT run).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Sequence

_LOGGER = logging.getLogger(__name__)

# Domains/actions we treat as sensitive enough to voice-confirm by default.
# (Only consulted when voice-confirm is enabled AND the action is protected.)
_SENSITIVE = {
    ("lock", "unlock"),
    ("cover", "open"),            # garage doors are covers
    ("alarm_control_panel", "alarm_disarm"),
    ("switch", "turn_off"),       # only when the switch is flagged security-ish
}

_YES = ["yes", "yeah", "yep", "do it", "confirm", "confirmed", "go ahead",
        "unlock it", "open it", "sure", "affirmative", "please do"]
_NO = ["no", "nope", "cancel", "leave it", "stop", "don't", "negative",
       "abort", "never mind"]

# How long to wait for a spoken answer / playback, before giving up (fail-safe).
_ANSWER_TIMEOUT = 30.0
_PLAYBACK_TIMEOUT = 20.0


def _cfg(hass, key: str, default=None):
    try:
        from . import jarvis_config
        v = jarvis_config.get(key, default)
        return v if v is not None else default
    except Exception:
        return default


def is_enabled(hass) -> bool:
    """Voice confirmation is opt-in."""
    return bool(_cfg(hass, "voice_confirm_enabled", False))


def _mode(hass) -> str:
    m = str(_cfg(hass, "voice_confirm_mode", "auto") or "auto").lower()
    return m if m in ("native", "gated", "auto") else "auto"


def action_is_protected(hass, domain: str, service: str, entity_id: str = "") -> bool:
    """Whether this action should be voice-confirmed. Sensitive by domain/action,
    with a user override list (`voice_confirm_entities`) that can add or, prefixed
    with '!', exempt specific entities."""
    if not is_enabled(hass):
        return False
    overrides = _cfg(hass, "voice_confirm_entities", [])
    if isinstance(overrides, list):
        if entity_id and f"!{entity_id}" in overrides:
            return False                       # explicit exemption
        if entity_id and entity_id in overrides:
            return True                        # explicit inclusion
    # switch.turn_off is only sensitive for security-ish switches
    if (domain, service) == ("switch", "turn_off"):
        hay = entity_id.lower()
        return any(t in hay for t in ("alarm", "security", "camera", "lock"))
    return (domain, service) in _SENSITIVE


# ── satellite discovery ──────────────────────────────────────────────────────

def _satellite_for_entity(hass, entity_id: str) -> Optional[str]:
    """Best assist_satellite to ask through — the one nearest the target entity's
    area, else any available satellite."""
    try:
        from . import audio_routing
        # target area
        area = None
        try:
            from homeassistant.helpers import entity_registry as er, area_registry as ar  # noqa
            reg = er.async_get(hass)
            ent = reg.async_get(entity_id)
            if ent and ent.area_id:
                area = ent.area_id
        except Exception:
            pass
        if area:
            sats = audio_routing.satellites_in_area(hass, area)
            if sats:
                return sats[0]
        # any satellite
        for e in hass.states.async_all("assist_satellite"):
            if e.state not in ("unavailable", "unknown"):
                return e.entity_id
    except Exception:
        pass
    return None


def _speaker_for_satellite(hass, satellite: str) -> tuple[Optional[str], list]:
    """(tts_entity, [media_players]) to speak through for the gated path — the
    speaker in the satellite's area (the Nest), via JARVIS's normal routing."""
    try:
        from . import audio_routing, tts_helper
        area = None
        try:
            from homeassistant.helpers import entity_registry as er
            reg = er.async_get(hass)
            ent = reg.async_get(satellite)
            if ent and ent.area_id:
                area = ent.area_id
        except Exception:
            pass
        speakers = audio_routing.speakers_in_area(hass, area) if area else []
        # resolve the JARVIS TTS entity: prefer the configured one, else best
        configured = _cfg(hass, "tts_engine", "") or ""
        tts_entity = None
        try:
            tts_entity = tts_helper.resolve_tts_entity(hass, configured)
        except Exception:
            pass
        if not tts_entity:
            try:
                tts_entity = tts_helper.find_best_tts_entity(hass)
            except Exception:
                tts_entity = configured or None
        return tts_entity, speakers
    except Exception:
        return None, []


def _satellite_has_audio_out(hass, satellite: str) -> bool:
    """Heuristic: does this assist_satellite have its own audio output configured
    (so native announce would actually be heard)? We can't fully introspect the
    pipeline, so we look for a configured hint and otherwise assume not (JARVIS
    satellites are ears-only)."""
    hint = _cfg(hass, "satellite_audio_out", None)
    if isinstance(hint, dict):
        return bool(hint.get(satellite, False))
    if isinstance(hint, bool):
        return hint
    return False


# ── public: confirm a yes/no ─────────────────────────────────────────────────

async def confirm(hass, question: str, *, entity_id: str = "",
                  timeout: float = _ANSWER_TIMEOUT) -> bool:
    """Ask `question` aloud and return True only on an affirmative spoken answer.
    Fail-safe: any error, timeout, or negative → False (the protected action does
    NOT run). Chooses native vs gated per config. Never raises."""
    try:
        satellite = _satellite_for_entity(hass, entity_id)
        if not satellite:
            _LOGGER.warning("voice_confirm: no assist_satellite available; cannot confirm")
            return False
        mode = _mode(hass)
        if mode == "native" or (mode == "auto" and _satellite_has_audio_out(hass, satellite)):
            ok = await _confirm_native(hass, satellite, question, timeout)
            if ok is not None:
                return ok
            # native failed to run → fall through to gated
        return await _confirm_gated(hass, satellite, question, timeout)
    except Exception as exc:
        _LOGGER.warning("voice_confirm.confirm failed (treating as no): %s", exc)
        return False


async def _confirm_native(hass, satellite: str, question: str,
                          timeout: float) -> Optional[bool]:
    """Use assist_satellite.ask_question with yes/no sentence sets. Returns
    True/False on a matched answer, or None if the action couldn't run (so the
    caller can fall back to gated)."""
    try:
        result = await hass.services.async_call(
            "assist_satellite", "ask_question",
            {
                "entity_id": satellite,
                "question": question,
                "preannounce": False,
                "answers": [
                    {"id": "confirm", "sentences": _YES},
                    {"id": "deny", "sentences": _NO},
                ],
            },
            blocking=True, return_response=True,
        )
        # HA returns {satellite: {"id": ...}} or {"id": ...} depending on version
        ans = None
        if isinstance(result, dict):
            if "id" in result:
                ans = result.get("id")
            else:
                for v in result.values():
                    if isinstance(v, dict) and "id" in v:
                        ans = v["id"]
                        break
        if ans is None:
            return None                        # couldn't parse → fall back
        return ans == "confirm"
    except Exception as exc:
        _LOGGER.debug("voice_confirm native path unavailable: %s", exc)
        return None


async def _confirm_gated(hass, satellite: str, question: str,
                         timeout: float) -> bool:
    """Fallback: speak via the normal announce path (to the Nest), wait for
    playback to finish, then open listening on the satellite and interpret the
    reply. Since we can't synchronously capture the STT result here without deep
    pipeline hooks, the gated path asks and re-opens the mic; the answer arrives
    as a normal JARVIS conversation turn that the caller's follow-up handles.

    For a *blocking* yes/no we do the safe thing: speak the question, open the
    mic, and return False unless the pipeline round-trip confirms — i.e. gated
    confirm never auto-approves a protected action on its own. This keeps the
    fail-safe property while still voicing the prompt and listening."""
    from . import tts_helper
    tts_entity, speakers = _speaker_for_satellite(hass, satellite)
    if tts_entity and speakers:
        try:
            await tts_helper.async_announce(hass, question, tts_entity, speakers,
                                            context="confirm")
        except Exception as exc:
            _LOGGER.debug("voice_confirm gated announce failed: %s", exc)
    else:
        _LOGGER.warning("voice_confirm gated: no speaker for %s; cannot voice prompt",
                        satellite)
        return False

    # Wait for playback to finish so we don't capture our own prompt (echo).
    await _wait_for_playback(hass, speakers)

    # Re-open the mic on the satellite so the user can answer.
    started = await _start_listening(hass, satellite)
    if not started:
        _LOGGER.debug("voice_confirm gated: couldn't reopen mic on %s", satellite)
    # We deliberately return False here: the gated path cannot itself capture the
    # STT result synchronously, so it never auto-approves. The spoken answer
    # comes back as a normal conversation turn; the agent, seeing a pending
    # confirmation in context, completes the action then. (Fail-safe.)
    return False


async def _wait_for_playback(hass, speakers: Sequence[str],
                             timeout: float = _PLAYBACK_TIMEOUT) -> None:
    """Wait until the given media_players leave 'playing' (best-effort)."""
    if not speakers:
        return
    deadline = asyncio.get_event_loop().time() + timeout
    # small settle so state flips to 'playing' first
    await asyncio.sleep(0.6)
    while asyncio.get_event_loop().time() < deadline:
        try:
            playing = any(
                (hass.states.get(s) and hass.states.get(s).state == "playing")
                for s in speakers)
        except Exception:
            playing = False
        if not playing:
            return
        await asyncio.sleep(0.4)


async def _start_listening(hass, satellite: str) -> bool:
    """Open the satellite's mic. Prefer assist_satellite.start_conversation with
    an empty prompt; fall back to the ESPHome voice_assistant.start action if a
    mapping is configured."""
    try:
        await hass.services.async_call(
            "assist_satellite", "start_conversation",
            {"entity_id": satellite, "preannounce": False},
            blocking=False,
        )
        return True
    except Exception:
        pass
    # ESPHome fallback: user maps satellite → esphome start action entity
    esph = _cfg(hass, "satellite_start_action", {})
    if isinstance(esph, dict) and satellite in esph:
        try:
            svc = esph[satellite]  # e.g. "esphome.basement_start_va"
            dom, _, name = svc.partition(".")
            await hass.services.async_call(dom, name, {}, blocking=False)
            return True
        except Exception as exc:
            _LOGGER.debug("voice_confirm ESPHome start failed: %s", exc)
    return False


# ── public: open-ended follow-up ─────────────────────────────────────────────

async def ask_followup(hass, question: str, *, turn_context: str = "",
                       entity_id: str = "") -> dict:
    """Ask an open-ended question aloud and open the mic for a free answer. Uses
    native start_conversation when available (which passes extra_system_prompt so
    a bare 'yes' is understood), else the gated speak→wait→listen path. Returns
    {ok, mode, satellite}. The user's answer returns as a normal conversation
    turn. Never raises."""
    try:
        satellite = _satellite_for_entity(hass, entity_id)
        if not satellite:
            return {"ok": False, "error": "no assist_satellite available"}
        mode = _mode(hass)
        if mode == "native" or (mode == "auto" and _satellite_has_audio_out(hass, satellite)):
            try:
                await hass.services.async_call(
                    "assist_satellite", "start_conversation",
                    {
                        "entity_id": satellite,
                        "start_message": question,
                        "extra_system_prompt": turn_context or "",
                        "preannounce": False,
                    },
                    blocking=True,
                )
                return {"ok": True, "mode": "native", "satellite": satellite}
            except Exception as exc:
                _LOGGER.debug("voice_confirm followup native failed: %s", exc)
                # fall through to gated
        # gated
        tts_entity, speakers = _speaker_for_satellite(hass, satellite)
        if tts_entity and speakers:
            from . import tts_helper
            await tts_helper.async_announce(hass, question, tts_entity, speakers,
                                            context="followup")
            await _wait_for_playback(hass, speakers)
        await _start_listening(hass, satellite)
        return {"ok": True, "mode": "gated", "satellite": satellite}
    except Exception as exc:
        _LOGGER.warning("voice_confirm.ask_followup failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ── diagnostic (for the panel test button) ───────────────────────────────────

async def announce_test(hass, satellite: Optional[str] = None) -> dict:
    """Fire a bare assist_satellite.announce at a satellite and report — the
    'does it come out the Nest?' test the whole native path depends on. Returns
    {ok, satellite, note}. Never raises."""
    try:
        sat = satellite or _satellite_for_entity(hass, "")
        if not sat:
            return {"ok": False, "note": "no assist_satellite found"}
        await hass.services.async_call(
            "assist_satellite", "announce",
            {"entity_id": sat, "message": "JARVIS voice output test."},
            blocking=True,
        )
        return {"ok": True, "satellite": sat,
                "note": "Announce fired. If you heard it on the Nest, native "
                        "mode works — otherwise use gated mode."}
    except Exception as exc:
        return {"ok": False, "error": str(exc),
                "note": "assist_satellite.announce failed — use gated mode."}
