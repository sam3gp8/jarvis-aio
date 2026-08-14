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
# Room-scoped attribution (v6.77.0): when a room's own presence/recognition
# says exactly one person is in it, an event in THAT room is very likely theirs
# even with several people home. This is the signal that unlocks per-person
# routines in a multi-occupant house, where sole-occupancy almost never holds.
_W_ROOM_SOLE = 0.75      # only this person detected in the event's room
_W_ROOM_PRESENT = 0.30   # this person is among those detected in the room
_W_PROXIMITY = 0.35      # nearest-person by device/BLE proximity to the area
_ROOM_FRESH_SECS = 300.0 # room recognition older than this stops counting
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

def _room_votes(hass: HomeAssistant, area_id: Optional[str], now: float) -> dict:
    """Who is detected in THIS room right now → {name: weight} (v6.77.0).

    Uses camera face recognition mapped to areas: if the only person recently
    recognised in the event's room is Eliana, a light change in that room is
    almost certainly hers even if three people are home. This is what makes
    per-person attribution work in a multi-occupant house — sole-occupancy
    across the whole house is rare; sole-occupancy of a ROOM is common."""
    votes: dict = {}
    if not area_id:
        return votes
    try:
        from . import recognition, audio_routing
        seen = recognition.who_is_where(hass) or {}   # {camera_entity: name}
        in_room: dict = {}
        for cam, name in seen.items():
            if not name or name == UNKNOWN:
                continue
            try:
                cam_area = audio_routing.entity_area(hass, cam)
            except Exception:
                cam_area = None
            if cam_area != area_id:
                continue
            rec = recognition.last_seen_at(hass, cam) or {}
            age = float(rec.get("age_seconds", 0.0))
            if age > _ROOM_FRESH_SECS:
                continue
            conf = float(rec.get("confidence", 0.7))
            recency = max(0.0, 1.0 - (age / _ROOM_FRESH_SECS))
            in_room[name] = max(in_room.get(name, 0.0), conf * recency)
        if not in_room:
            return votes
        # exactly one person seen in the room ⇒ strong; several ⇒ weaker each
        weight = _W_ROOM_SOLE if len(in_room) == 1 else _W_ROOM_PRESENT
        for name, strength in in_room.items():
            votes[name] = weight * max(0.25, strength)
    except Exception as exc:
        _LOGGER.debug("identity: room votes failed: %s", exc)
    return votes


def _proximity_votes(hass: HomeAssistant, area_id: Optional[str]) -> dict:
    """Nearest-person by device/BLE proximity to the event's area (v6.77.0).

    Reads device_tracker entities that report an area or a BLE-proxy room
    (e.g. Bermuda/ESPresense style trackers whose state names a room). Only
    contributes when a tracker actually resolves to the same area."""
    votes: dict = {}
    if not area_id:
        return votes
    try:
        from . import audio_routing
        for st in hass.states.async_all("device_tracker"):
            name = (st.attributes.get("friendly_name") or "").strip()
            owner = st.attributes.get("person") or _owner_from_tracker(hass, st.entity_id)
            who = owner or name
            if not who:
                continue
            # a tracker whose STATE is a room name, or whose entity sits in the area
            state_area = str(st.state or "").strip().lower().replace(" ", "_")
            try:
                ent_area = audio_routing.entity_area(hass, st.entity_id)
            except Exception:
                ent_area = None
            if state_area and state_area == str(area_id).lower():
                votes[normalize(who)] = max(votes.get(normalize(who), 0.0), _W_PROXIMITY)
            elif ent_area and ent_area == area_id:
                votes[normalize(who)] = max(votes.get(normalize(who), 0.0),
                                            _W_PROXIMITY * 0.6)
    except Exception as exc:
        _LOGGER.debug("identity: proximity votes failed: %s", exc)
    return votes


def _owner_from_tracker(hass: HomeAssistant, tracker_id: str) -> Optional[str]:
    """Find which person entity claims this device_tracker."""
    try:
        for st in hass.states.async_all("person"):
            devs = st.attributes.get("device_trackers") or []
            if tracker_id in devs:
                return st.attributes.get("friendly_name") or st.entity_id.split(".")[-1]
    except Exception:
        pass
    return None


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

    # Tier 2b — ROOM-SCOPED signals (v6.77.0). resolve() has always accepted an
    # area_id; now it uses it. Who is in the room where the event happened is
    # the strongest practical signal in a multi-occupant house.
    for name, w in _room_votes(hass, area_id, now).items():
        votes[normalize(name)] += w
        methods.add("room")

    # Tier 2c — device/BLE proximity to that room
    for name, w in _proximity_votes(hass, area_id).items():
        votes[name] += w
        methods.add("proximity")

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


def quick_person(hass: HomeAssistant, area_id: Optional[str] = None) -> str:
    """
    Cheap identity for high-volume callers (the state-change listener). As of
    v6.77.0 this is ROOM-AWARE: pass the event's area_id and room-scoped
    presence/recognition is used, so attribution works in a multi-occupant house
    instead of only when exactly one person is home. Falls back to the old
    sole-occupant behaviour when no area is known or nothing resolves.
    """
    return quick_identify(hass, area_id).person


def quick_identify(hass: HomeAssistant, area_id: Optional[str] = None) -> Identification:
    """Room-aware identity WITH confidence and candidates (v6.77.0).

    Returns a full Identification so callers can store partial certainty
    ("probably Sam, 0.62") instead of discarding everything short of certain.
    Deliberately skips the expensive voice tier — this runs on every state
    change — but does use presence, face, room, and proximity signals."""
    if not bool(_cfg("identity_enabled", True)):
        return Identification(UNKNOWN, 0.0, "disabled")
    try:
        room_ident = None
        if area_id:
            # full fusion (minus voice, which resolve() gates behind config)
            room_ident = resolve(hass, area_id=area_id)
            if room_ident.known:
                return room_ident
            # room resolution inconclusive — fall through to whole-home presence
            # so a sole occupant is still attributed (v6.85.0). Returning
            # 'unknown' here starved single-person homes of attribution, so no
            # per-person routines ever formed.
        home = _home_people(hass)
        if len(home) == 1:
            return Identification(home[0], _W_SOLE_OCCUPANT, "sole_occupant",
                                  {home[0]: _W_SOLE_OCCUPANT})
        # multi-occupant: keep the room's probable candidates if resolve found any
        if room_ident is not None and room_ident.candidates:
            return room_ident
        if len(home) > 1:
            cands = {n: _W_HOME_PRIOR for n in home}
            return Identification(UNKNOWN, 0.0, "ambiguous_home", cands)
    except Exception as exc:
        _LOGGER.debug("identity: quick_identify failed: %s", exc)
    return Identification(UNKNOWN, 0.0, "no_signal")
