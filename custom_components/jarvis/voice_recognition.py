"""
JARVIS voice recognition — makes JARVIS know who it's talking to by *voice*, and
lets it learn people's voices over time through natural speech.

Design (deliberately): JARVIS does NOT run a speaker-embedding model inside the
Home Assistant process — that's heavy, dependency-laden, and needs the raw
utterance audio. Instead it delegates the embedding + enrollment to a dedicated
speaker-recognition service and *consumes* its "who is speaking" result. Any
backend works as long as it publishes the current speaker to Home Assistant:

  • VoiceBM (github.com/cybericebyte/VoiceBM) — Sherpa-ONNX, publishes over MQTT;
    exposes a per-person ``binary_sensor.<person>_voice`` (ON while they speak)
    and a ``sensor.*current_speaker``.
  • speaker-recognition (github.com/EuleMitKeule/speaker-recognition) —
    Resemblyzer, exposes a current-speaker sensor via its HA integration.
  • anything else that surfaces a current-speaker entity.

This module turns that entity into a vote for the identity resolver's voice tier
(the strongest tier — voice is the most direct "who's speaking" signal), via the
seam ``identity.register_voice_provider``.

Learning over time: the service does the actual enrollment, but JARVIS supplies
the hard part for hands-free enrollment — the *label*. When JARVIS is already
confident who's speaking from its other signals (sole occupant, or a recent
camera face match) but the voice service doesn't recognize the voice yet, that
utterance is an enrollment opportunity with a known answer. ``enrollment_candidate``
surfaces it so an automation can enroll the pending sample under the right
person — so voice profiles build themselves from ordinary conversation.

Config (jarvis_config):
  identity_voice_fingerprint    master enable for the identity voice tier
  voice_recognition_source      a current-speaker sensor entity_id, OR a glob for
                                per-person sensors, e.g. "binary_sensor.*_voice"
  voice_recognition_confidence  fallback confidence (0..1) when the source carries
                                no score of its own (default 0.85)
"""
from __future__ import annotations

import fnmatch
import logging
import time
from typing import Optional

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# States that mean "no one is speaking / not identified".
_NO_SPEAKER = ("", "unknown", "unavailable", "none", "no_speaker",
               "not_speaking", "idle", "off")
_RECENCY_WINDOW = 20.0  # seconds; a current-speaker sensor older than this is stale
_SCORE_ATTRS = ("confidence", "score", "probability", "similarity")


def _cfg(key: str, default):
    try:
        from . import jarvis_config
        return jarvis_config.get(key, default)
    except Exception:
        return default


def normalize(name: str) -> str:
    from . import identity
    return identity.normalize(name)


def _score_from(st, default: float) -> float:
    """Read a 0..1 confidence from the entity's attributes, else the default.
    Accepts 0..1 or 0..100 (percent) and clamps."""
    for key in _SCORE_ATTRS:
        val = st.attributes.get(key)
        if val is None:
            continue
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        if v > 1.0:
            v = v / 100.0
        return max(0.0, min(1.0, v))
    return default


def _fresh(st, window: float = _RECENCY_WINDOW) -> bool:
    """Best-effort recency: don't attribute a stale 'last speaker' to a fresh
    command. If no timestamp is available, assume fresh."""
    try:
        from datetime import datetime, timezone
        ts = getattr(st, "last_updated", None) or getattr(st, "last_changed", None)
        if ts is None:
            return True
        return (datetime.now(timezone.utc) - ts).total_seconds() <= window
    except Exception:
        return True


def _person_from_entity(st, pattern: str) -> str:
    """Derive a person id from a per-person voice sensor. Prefers the friendly
    name; falls back to the entity id with the domain and a trailing 'voice'/
    'speaking' token stripped (binary_sensor.sam_voice -> sam)."""
    fname = st.attributes.get("friendly_name")
    if fname:
        cleaned = fname.lower()
        for suffix in (" voice", " speaking", " is speaking"):
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
        return cleaned.strip()
    slug = st.entity_id.split(".", 1)[-1]
    for suffix in ("_voice", "_speaking", "_is_speaking"):
        if slug.endswith(suffix):
            slug = slug[: -len(suffix)]
    return slug


def identify(hass: HomeAssistant, device_id: Optional[str] = None) -> dict:
    """The voice provider: {person -> score} for whoever is speaking now, read
    from the configured speaker-recognition entity. Empty when nobody is
    identified. Registered into identity's voice tier."""
    src = (_cfg("voice_recognition_source", "") or "").strip()
    if not src:
        return {}
    conf = float(_cfg("voice_recognition_confidence", 0.85))

    # Per-person sensors (ON while that person is speaking) — inherently current.
    if any(ch in src for ch in "*?[") or src.endswith("_voice"):
        votes: dict = {}
        for st in hass.states.async_all("binary_sensor"):
            if not fnmatch.fnmatch(st.entity_id, src):
                continue
            if str(st.state).lower() != "on":
                continue
            person = _person_from_entity(st, src)
            if person:
                votes[person] = max(votes.get(person, 0.0), _score_from(st, conf))
        return votes

    # A single current-speaker sensor whose state is the speaker's name.
    st = hass.states.get(src)
    if st is None:
        return {}
    name = str(st.state).strip()
    if name.lower() in _NO_SPEAKER:
        return {}
    if not _fresh(st):
        return {}
    return {name: _score_from(st, conf)}


def enrollment_candidate(hass: HomeAssistant,
                         device_id: Optional[str] = None) -> Optional[str]:
    """A person JARVIS is confident about from its *other* signals (sole
    occupant / recent face) but whose voice the service doesn't recognize yet —
    a labelled opportunity to enroll their voice. Returns the person id, or None.

    This is what lets voice profiles build from natural speech: the answer is
    known, so the pending sample can be enrolled under the right person with no
    manual review."""
    from . import identity
    ident = identity.resolve(hass, device_id=device_id)
    if not ident.known:
        return None                      # we don't know who it is either — skip
    if "voice" in ident.method:
        return None                      # voice already recognizes them
    known_to_voice = {normalize(k) for k in identify(hass, device_id)}
    if normalize(ident.person) in known_to_voice:
        return None                      # already enrolled
    return ident.person


# ── registration into the identity resolver's voice tier ─────────────────────

_ENROLL_COOLDOWN = 300.0        # seconds between enroll prompts for one person
_last_enroll_fire: dict = {}


def maybe_fire_enrollment(hass: HomeAssistant,
                          device_id: Optional[str] = None) -> Optional[str]:
    """If there's a labelled enrollment opportunity, fire a rate-limited
    ``jarvis_voice_enroll_candidate`` event so an automation can enroll the
    pending sample under the right person. This is the hands-free-learning
    trigger. Returns the person if it fired, else None. Cheap; safe per turn."""
    if not bool(_cfg("identity_voice_fingerprint", False)):
        return None
    if not (_cfg("voice_recognition_source", "") or "").strip():
        return None
    if not (_cfg("voice_recognition_auto_enroll", True)):
        return None
    person = enrollment_candidate(hass, device_id)
    if not person:
        return None
    now = time.time()
    key = normalize(person)
    if now - _last_enroll_fire.get(key, 0.0) < _ENROLL_COOLDOWN:
        return None
    _last_enroll_fire[key] = now
    try:
        hass.bus.async_fire("jarvis_voice_enroll_candidate",
                            {"person": person, "device_id": device_id})
        _LOGGER.info("Voice enrollment opportunity for %s", person)
    except Exception as exc:
        _LOGGER.debug("enroll candidate fire failed: %s", exc)
    return person


def register(hass: HomeAssistant) -> None:
    """Wire this provider into identity's voice tier. Safe to call once at setup;
    actual voting is still gated by `identity_voice_fingerprint` and a configured
    `voice_recognition_source`."""
    try:
        from . import identity
        identity.register_voice_provider(lambda h, dev: identify(h, dev))
        _LOGGER.debug("Voice recognition provider registered")
    except Exception as exc:
        _LOGGER.warning("Voice recognition registration failed: %s", exc)


def unregister() -> None:
    try:
        from . import identity
        identity.register_voice_provider(None)
    except Exception:
        pass
