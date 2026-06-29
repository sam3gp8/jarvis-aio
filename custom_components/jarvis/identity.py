"""
JARVIS identity resolver — "who is JARVIS talking to right now?"

Per-person identity is the keystone the curated knowledge store's `subject` field
was built for. This resolver fuses the signals already available in the home into
a best-guess person + confidence, and is deliberately *tiered* so it works for
everyone, with or without a GPU:

  Tier 1 — presence            (no GPU)   who's home, via HA person.* entities.
                                          A sole occupant is a strong signal.
  Tier 2 — recent face         (optional) Frigate/DoubleTake recognitions from
                                          recognition.py. Runs on Frigate, not the
                                          HA box — no local GPU needed.
  Tier 3 — voice fingerprint   (optional, GPU) a pluggable provider, OFF by
                                          default. When a GPU voice-id backend is
                                          registered it contributes the strongest
                                          vote (voice is the most direct "who's
                                          speaking" signal). Until then: no-op.

The default tiers (presence + face) give a non-power-user a working identity
system with zero setup and no GPU. Power users add accuracy by enabling the
voice-fingerprint tier once their GPU server is online.

Config (all via jarvis_config, sensible defaults):
  identity_enabled            (default True)
  identity_min_confidence     (default 0.45)  below ⇒ "unknown"
  identity_voice_fingerprint  (default False) the GPU tier
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

UNKNOWN = "unknown"
DEFAULT_PERSONAL_SUBJECT = "primary"  # fallback knowledge subject when unresolved

# Tier weights (the most a tier can contribute toward a single person).
_W_SOLE_OCCUPANT = 0.6
_W_HOME_PRIOR = 0.15
_W_FACE = 0.8
_W_VOICE = 0.9
_FACE_RECENCY_WINDOW = 300.0  # seconds; a face older than this carries no weight


@dataclass
class Identification:
    person: str = UNKNOWN
    confidence: float = 0.0
    method: str = "none"
    candidates: dict = field(default_factory=dict)

    @property
    def known(self) -> bool:
        return self.person != UNKNOWN


# ── voice-fingerprint seam (GPU provider plugs in here) ──────────────────────

_VOICE_PROVIDER: Optional[Callable[[HomeAssistant, Optional[str]], dict]] = None


def register_voice_provider(fn: Optional[Callable[[HomeAssistant, Optional[str]], dict]]) -> None:
    """
    Register a voice-fingerprint backend. `fn(hass, device_id) -> {person: score}`
    with score in 0..1. Pass None to clear. This is the seam a local GPU voice-id
    model plugs into; until one is registered the voice tier contributes nothing.
    """
    global _VOICE_PROVIDER
    _VOICE_PROVIDER = fn


def has_voice_provider() -> bool:
    return _VOICE_PROVIDER is not None


def _voice_votes(hass: HomeAssistant, device_id: Optional[str]) -> dict:
    if _VOICE_PROVIDER is None:
        return {}
    try:
        return _VOICE_PROVIDER(hass, device_id) or {}
    except Exception as exc:
        _LOGGER.debug("identity: voice provider failed: %s", exc)
        return {}


# ── helpers ──────────────────────────────────────────────────────────────────

def normalize(name: str) -> str:
    """A stable subject id from a display name (e.g. 'Sam Smith' -> 'sam_smith')."""
    return "_".join((name or "").strip().lower().split())


def subject_for(ident: Identification) -> str:
    """The knowledge `subject` to attribute to — the person, or the default."""
    return normalize(ident.person) if ident.known else DEFAULT_PERSONAL_SUBJECT


def _cfg(key: str, default):
    try:
        from . import jarvis_config
        return jarvis_config.get(key, default)
    except Exception:
        return default


def _home_people(hass: HomeAssistant) -> list[str]:
    try:
        from . import presence
        summary = presence.get_presence_summary(hass)
        return [p["name"] for p in summary.get("people", [])
                if p.get("state") == "home" and p.get("name")]
    except Exception as exc:
        _LOGGER.debug("identity: presence read failed: %s", exc)
        return []


def _face_votes(hass: HomeAssistant, now: float) -> dict:
    """Recent Frigate/DoubleTake recognitions → {name: weight}."""
    votes: dict = {}
    try:
        from . import recognition
        seen = recognition.who_is_where(hass)  # {camera_entity: name}
        for cam, name in seen.items():
            rec = recognition.last_seen_at(hass, cam) or {}
            conf = float(rec.get("confidence", 0.7))
            age = float(rec.get("age_seconds", 0.0))
            recency = max(0.0, 1.0 - age / _FACE_RECENCY_WINDOW)
            if recency > 0 and name:
                votes[name] = max(votes.get(name, 0.0), _W_FACE * conf * recency)
    except Exception as exc:
        _LOGGER.debug("identity: face read failed: %s", exc)
    return votes


# ── resolve ──────────────────────────────────────────────────────────────────

def resolve(hass: HomeAssistant, *, device_id: Optional[str] = None,
            area_id: Optional[str] = None, now: Optional[float] = None) -> Identification:
    """
    Fuse available signals into a best-guess person + confidence. Returns an
    Identification with person == 'unknown' when nothing is confident enough.
    Pure-ish (reads hass state); safe to call on every turn.
    """
    if not bool(_cfg("identity_enabled", True)):
        return Identification(UNKNOWN, 0.0, "disabled")

    now = now if now is not None else time.time()
    votes: dict = defaultdict(float)
    methods: set = set()

    # Tier 1 — presence
    home = _home_people(hass)
    if len(home) == 1:
        votes[home[0]] += _W_SOLE_OCCUPANT
        methods.add("sole_occupant")
    elif len(home) > 1:
        for name in home:
            votes[name] += _W_HOME_PRIOR
        methods.add("home_prior")

    # Tier 2 — recent face (optional)
    for name, w in _face_votes(hass, now).items():
        votes[name] += w
        methods.add("face")

    # Tier 3 — voice fingerprint (optional, GPU)
    if bool(_cfg("identity_voice_fingerprint", False)):
        for name, score in _voice_votes(hass, device_id).items():
            votes[name] += _W_VOICE * float(score)
            methods.add("voice")

    if not votes:
        return Identification(UNKNOWN, 0.0, "no_signal")

    ranked = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
    person, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0

    # Confidence rewards both absolute evidence AND a decisive margin over the
    # runner-up. A lone weak vote, or a near-tie, stays low.
    decisiveness = (top - second) / top if top > 0 else 0.0
    confidence = min(1.0, top) * (0.5 + 0.5 * decisiveness)
    candidates = {k: round(v, 3) for k, v in ranked}

    min_conf = float(_cfg("identity_min_confidence", 0.45))
    if confidence < min_conf:
        return Identification(UNKNOWN, round(confidence, 3), "low_confidence", candidates)

    return Identification(person, round(confidence, 3),
                          "+".join(sorted(methods)), candidates)


def resolve_subject(hass: HomeAssistant, **kwargs) -> str:
    """Convenience: resolve and return the knowledge subject id directly."""
    return subject_for(resolve(hass, **kwargs))
