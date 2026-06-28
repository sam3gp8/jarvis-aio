"""
JARVIS — Audio routing (v5.7.00, area-registry-driven).

HA's area registry is the source of truth. JARVIS reads areas, the entities
assigned to each, and routes audio based on what's physically where.

What JARVIS recognizes per area:
  - SATELLITES:  entities with domain 'assist_satellite' (ears, never output)
  - SPEAKERS:    entities with domain 'media_player' (mouths)
  - PRESENCE:    entities with domain 'binary_sensor', device_class 'occupancy'
                 (mmWave, motion, or any occupancy detector)

The user's ONLY routing config is:
  - bedroom_areas:  list of HA area_ids flagged for sleep detection
  - broadcast_group: single media_player entity for critical/high urgency
                     (typically the "home" Cast group)

Routing rules:

  REPLY (someone spoke to a satellite):
    1. Get the satellite's area.
    2. Find a media_player in that same area → speak there.
    3. If no speaker in that area → speak through the satellite itself
       (its own built-in speaker). Per Sam's directive: "if the satellite
       is the only thing in earshot, it wins."

  OBSERVER (proactive announcement):
    Urgency CRITICAL (smoke/leak/door forced):
      → broadcast_group, overrides sleep state, always
    Urgency HIGH (doorbell, security event):
      → broadcast_group if someone is home; else mobile notification
    Urgency MEDIUM:
      → speaker in the room where presence is detected;
         fallback to broadcast_group if no presence detected and someone is home;
         fallback to mobile notification if nobody home
    Urgency LOW:
      → speaker in the room where presence is detected;
         silent (queue) if no local presence — don't interrupt from another room

  BROADCAST (briefing, sentinel, doorbell — explicit group announcement):
      → broadcast_group; if not set, fall back to all media_players across all
         non-bedroom areas (aggregate).

Routing always EXCLUDES voice satellites from being output targets, except
the explicit "satellite fallback" case in REPLY above.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

_LOGGER = logging.getLogger(__name__)


# ─── Entity / area resolution helpers ────────────────────────────────────────

def entity_area(hass: HomeAssistant, entity_id: str) -> Optional[str]:
    """Return the HA area_id for an entity (direct, or via its device).
    Returns None if no area is assigned."""
    try:
        ent_reg = er.async_get(hass)
        ent = ent_reg.async_get(entity_id)
        if ent is None:
            return None
        if ent.area_id:
            return ent.area_id
        if ent.device_id:
            dev_reg = dr.async_get(hass)
            dev = dev_reg.async_get(ent.device_id)
            if dev and dev.area_id:
                return dev.area_id
    except Exception as exc:
        _LOGGER.debug("area lookup failed for %s: %s", entity_id, exc)
    return None


def _entities_by_domain(
    hass: HomeAssistant, domain: str
) -> list[str]:
    """Return all entity_ids for a given domain."""
    return [s.entity_id for s in hass.states.async_all(domain)]


def _entities_by_domain_and_device_class(
    hass: HomeAssistant, domain: str, device_class: str
) -> list[str]:
    """Return all entity_ids for (domain, device_class)."""
    out = []
    for s in hass.states.async_all(domain):
        dc = s.attributes.get("device_class")
        if dc == device_class:
            out.append(s.entity_id)
    return out


# ─── Per-area discovery ──────────────────────────────────────────────────────

def satellites_in_area(hass: HomeAssistant, area_id: str) -> list[str]:
    """All assist_satellite entities whose area matches."""
    return [
        e for e in _entities_by_domain(hass, "assist_satellite")
        if entity_area(hass, e) == area_id
    ]


def speakers_in_area(hass: HomeAssistant, area_id: str) -> list[str]:
    """All media_player entities whose area matches."""
    return [
        e for e in _entities_by_domain(hass, "media_player")
        if entity_area(hass, e) == area_id
    ]


def presence_entities_in_area(hass: HomeAssistant, area_id: str) -> list[str]:
    """All binary_sensor.*_occupancy entities (device_class='occupancy' or
    'motion' or 'presence') in the given area."""
    occupancy_classes = ("occupancy", "motion", "presence")
    all_bs = _entities_by_domain(hass, "binary_sensor")
    out = []
    for e in all_bs:
        state = hass.states.get(e)
        if state is None:
            continue
        dc = state.attributes.get("device_class")
        if dc not in occupancy_classes:
            continue
        if entity_area(hass, e) == area_id:
            out.append(e)
    return out


def all_areas_with_satellite(hass: HomeAssistant) -> set[str]:
    """Areas that have at least one satellite. Used to iterate over
    'rooms JARVIS can hear from'."""
    out = set()
    for e in _entities_by_domain(hass, "assist_satellite"):
        area = entity_area(hass, e)
        if area:
            out.add(area)
    return out


def all_areas_with_presence(hass: HomeAssistant) -> set[str]:
    """Areas that have at least one occupancy/motion/presence sensor."""
    occupancy_classes = ("occupancy", "motion", "presence")
    out = set()
    for s in hass.states.async_all("binary_sensor"):
        dc = s.attributes.get("device_class")
        if dc not in occupancy_classes:
            continue
        area = entity_area(hass, s.entity_id)
        if area:
            out.add(area)
    return out


# ─── Presence / occupancy state ──────────────────────────────────────────────

def is_area_occupied(hass: HomeAssistant, area_id: str) -> bool:
    """Any presence sensor in this area currently reporting 'on'?"""
    for e in presence_entities_in_area(hass, area_id):
        state = hass.states.get(e)
        if state is None:
            continue
        if str(state.state).lower() in ("on", "home", "detected", "true", "occupied"):
            return True
    return False


def currently_occupied_areas(hass: HomeAssistant) -> list[str]:
    """All areas with at least one 'on' presence sensor right now."""
    return [
        area for area in all_areas_with_presence(hass)
        if is_area_occupied(hass, area)
    ]


def anyone_home(hass: HomeAssistant) -> bool:
    """True if anyone appears to be home: any occupancy/motion/presence sensor on
    ANYWHERE (area assignment is not required — a motion sensor with no area still
    proves presence), or any person/device_tracker reading 'home'. Guides the
    'while no one is home' announcement clause and medium/high fallback routing."""
    occ_classes = ("occupancy", "motion", "presence")
    on_states = ("on", "home", "detected", "true", "occupied")
    for s in hass.states.async_all("binary_sensor"):
        if (s.attributes.get("device_class") in occ_classes
                and str(s.state).lower() in on_states):
            return True
    for s in hass.states.async_all("person"):
        if str(s.state).lower() == "home":
            return True
    for s in hass.states.async_all("device_tracker"):
        if str(s.state).lower() == "home":
            return True
    return False


# ─── Routing: reply (direct speech) ──────────────────────────────────────────

# Integration platforms we prefer for replies — these are "real" speakers
# (Google Home, Nest, Chromecast, Alexa, Sonos) as opposed to the ESP32
# satellite's built-in media_player. Ordering matters — earlier = higher priority.
PREFERRED_SPEAKER_PLATFORMS = (
    "cast",            # Google Home / Chromecast / Nest Audio
    "google_assistant_sdk",
    "nest",
    "sonos",
    "alexa_media",
    "spotify",
    "squeezebox",
    "dlna_dmr",
)


def _satellite_device_ids_in_area(hass: HomeAssistant, area_id: str) -> set[str]:
    """
    Return the set of device_ids for all assist_satellite entities in this area.

    Used to identify media_player entities that belong to the same physical
    device as a satellite (e.g. the ESP32-S3-BOX-3's built-in speaker exposed
    as `media_player.jarvis_speaker`). We want to EXCLUDE those from reply
    routing because they're tinny — we want the Google Home in the room.
    """
    device_ids: set[str] = set()
    try:
        ent_reg = er.async_get(hass)
        for sat_id in satellites_in_area(hass, area_id):
            ent = ent_reg.async_get(sat_id)
            if ent and ent.device_id:
                device_ids.add(ent.device_id)
    except Exception as exc:
        _LOGGER.debug("_satellite_device_ids_in_area failed: %s", exc)
    return device_ids


def _mp_platform(hass: HomeAssistant, mp_entity_id: str) -> str:
    """Return the integration platform name for a media_player ('cast', etc.)."""
    try:
        ent_reg = er.async_get(hass)
        ent = ent_reg.async_get(mp_entity_id)
        if ent:
            return (ent.platform or "").lower()
    except Exception:
        pass
    return ""


def _mp_device_id(hass: HomeAssistant, mp_entity_id: str) -> Optional[str]:
    """Return the device_id for a media_player entity."""
    try:
        ent_reg = er.async_get(hass)
        ent = ent_reg.async_get(mp_entity_id)
        if ent:
            return ent.device_id
    except Exception:
        pass
    return None


def reply_target(
    hass: HomeAssistant,
    *,
    satellite_entity_id: Optional[str] = None,
    device_id: Optional[str] = None,
    satellite_pairings: Optional[dict[str, str]] = None,
) -> Optional[str]:
    """
    Pick ONE speaker for a direct reply.

    Priority (v5.7+):
      0. Explicit satellite_pairings override from panel Settings (if set).
      1. A real speaker in the same area as the satellite — Google/Nest/Sonos
         preferred over unknown platforms.
      2. Any other media_player in the area that is NOT the satellite's own
         built-in speaker (same device_id).
      3. The satellite itself (its built-in speaker) if nothing else available.
      4. None — caller should handle silence.

    Takes either a satellite entity_id (preferred) or device_id.
    satellite_pairings is {satellite_entity_id: media_player_entity_id}.
    """
    sat_entity = satellite_entity_id
    sat_area = None

    if satellite_entity_id:
        sat_area = entity_area(hass, satellite_entity_id)
    elif device_id:
        try:
            dev_reg = dr.async_get(hass)
            dev = dev_reg.async_get(device_id)
            if dev:
                sat_area = dev.area_id
            # Resolve the satellite entity_id from device_id for pairing lookup
            ent_reg = er.async_get(hass)
            for ent in ent_reg.entities.values():
                if ent.device_id == device_id and ent.domain == "assist_satellite":
                    sat_entity = ent.entity_id
                    break
        except Exception as exc:
            _LOGGER.debug("device_id->area lookup failed: %s", exc)

    # ── Priority 0: explicit panel pairing ────────────────────────────────
    _LOGGER.warning(
        "reply_target ENTRY: sat_entity=%s, device_id=%s, sat_area=%s, "
        "pairings=%s",
        sat_entity, device_id, sat_area,
        list(satellite_pairings.keys()) if satellite_pairings else None,
    )
    if satellite_pairings and sat_entity and sat_entity in satellite_pairings:
        paired = satellite_pairings[sat_entity]
        if paired and hass.states.get(paired):
            _LOGGER.debug(
                "reply_target: using panel pairing %s → %s",
                sat_entity, paired,
            )
            return paired
        _LOGGER.debug(
            "reply_target: panel pairing %s → %s but entity unavailable, "
            "falling through to area registry",
            sat_entity, paired,
        )

    if not sat_area:
        _LOGGER.debug("reply_target: could not resolve satellite area")
        return satellite_entity_id  # fall back: speak through satellite itself

    all_mps = speakers_in_area(hass, sat_area)
    if not all_mps:
        _LOGGER.debug(
            "reply_target: no media_player in area '%s', replying through satellite",
            sat_area,
        )
        return satellite_entity_id

    # Exclude satellite-bound media_players (ESP32 built-in speakers)
    sat_device_ids = _satellite_device_ids_in_area(hass, sat_area)
    real_mps = []
    for mp in all_mps:
        if mp.startswith("assist_satellite."):
            continue
        mp_dev = _mp_device_id(hass, mp)
        if mp_dev and mp_dev in sat_device_ids:
            _LOGGER.debug(
                "reply_target: skipping %s (shares device_id with satellite)",
                mp,
            )
            continue
        real_mps.append(mp)

    if not real_mps:
        _LOGGER.debug(
            "reply_target: area '%s' has only satellite-bound speakers, falling back",
            sat_area,
        )
        return satellite_entity_id

    # Prefer known-good speaker platforms (Cast/Nest/Sonos/etc.) in order
    for preferred_platform in PREFERRED_SPEAKER_PLATFORMS:
        for mp in real_mps:
            if _mp_platform(hass, mp) == preferred_platform:
                _LOGGER.debug(
                    "reply_target: chose %s (platform=%s) in area '%s'",
                    mp, preferred_platform, sat_area,
                )
                return mp

    # No preferred platform matched — return any real media_player
    _LOGGER.debug(
        "reply_target: no preferred-platform speaker, using %s in area '%s'",
        real_mps[0], sat_area,
    )
    return real_mps[0]


def reply_targets(
    hass: HomeAssistant,
    *,
    device_id: Optional[str] = None,
    voice_satellites: Optional[list] = None,       # deprecated, ignored
    reply_speakers: Optional[list] = None,          # deprecated, ignored
    broadcast_speakers: Optional[list] = None,      # deprecated, ignored
    legacy_cast_speakers: Optional[list] = None,    # deprecated, ignored
    room_routing: bool = True,                       # deprecated, always on in v5.3
    satellite_pairings: Optional[dict[str, str]] = None,
) -> list[str]:
    """
    v5.2 backward-compatibility shim — callers passed flat entity lists.

    v5.3 ignores the flat lists and uses HA's area registry. Returns a
    single-item list for consistency with the old signature.

    If device_id is provided, looks up the satellite/speaker in the device's
    area. Otherwise falls back to broadcast targets.
    """
    target = reply_target(
        hass, device_id=device_id, satellite_pairings=satellite_pairings,
    ) if device_id else None
    return [target] if target else broadcast_target(hass)


# ─── Routing: observer mode (proactive) ──────────────────────────────────────

def observer_speak_target(
    hass: HomeAssistant,
    *,
    urgency: str,
    broadcast_group: Optional[str] = None,
    announcement_speakers: Optional[list[str]] = None,
    is_sleeping: bool = False,
) -> tuple[list[str], str]:
    """
    Decide where to speak for an observer-mode announcement.

    Returns (targets, mode) where:
      - targets is a list of media_player entity_ids (may be empty)
      - mode is one of: "local", "broadcast", "notify_only", "suppressed"

    Caller uses `mode` to decide whether to also send a phone notification
    ("notify_only" means audio is suppressed, only the notification fires).

    announcement_speakers: explicit list from the panel Settings toggle.
    When set and non-empty, this overrides broadcast_group for broadcast-mode
    announcements (but NOT for local/room routing).

    Rules:
      CRITICAL: always broadcast (announcement_speakers > broadcast_group > all speakers); overrides sleep
      HIGH:     sleeping → notify only; awake+home → broadcast; away → notify
      MEDIUM:   sleeping → suppressed; present in room → room speaker;
                home but no room presence → broadcast; away → notify
      LOW:      sleeping → suppressed; present in room → room speaker;
                otherwise suppressed (queued — don't interrupt from elsewhere)
    """

    def _broadcast_speakers() -> list[str]:
        """Resolve broadcast speakers: panel toggles > broadcast_group > all."""
        if announcement_speakers:
            # Validate they still exist in HA
            valid = [s for s in announcement_speakers if hass.states.get(s)]
            if valid:
                return valid
        if broadcast_group:
            if hass.states.get(broadcast_group):
                return [broadcast_group]
        # Fallback: all non-satellite speakers
        return [
            s for s in _entities_by_domain(hass, "media_player")
            if not s.startswith("assist_satellite.")
        ]

    # ─── CRITICAL ────────────────────────────────────────────────────────────
    if urgency == "critical":
        speakers = _broadcast_speakers()
        return (speakers, "broadcast") if speakers else ([], "notify_only")

    home = anyone_home(hass)

    # ─── HIGH ────────────────────────────────────────────────────────────────
    if urgency == "high":
        if is_sleeping or not home:
            return ([], "notify_only")
        speakers = _broadcast_speakers()
        if speakers:
            return (speakers, "broadcast")
        # no broadcast speakers at all: fall back to room speaker if possible
        occupied = currently_occupied_areas(hass)
        targets = []
        for area in occupied:
            for spk in speakers_in_area(hass, area):
                if not spk.startswith("assist_satellite."):
                    targets.append(spk)
        return (targets, "broadcast") if targets else ([], "notify_only")

    # ─── MEDIUM ──────────────────────────────────────────────────────────────
    if urgency == "medium":
        if is_sleeping:
            return ([], "suppressed")
        if not home:
            return ([], "notify_only")
        occupied = currently_occupied_areas(hass)
        if occupied:
            # Speak in the first occupied area with a speaker
            for area in occupied:
                speakers = [
                    s for s in speakers_in_area(hass, area)
                    if not s.startswith("assist_satellite.")
                ]
                if speakers:
                    return ([speakers[0]], "local")
        # Home but no room-level presence (could be shared state / person sensor)
        speakers = _broadcast_speakers()
        if speakers:
            return (speakers, "broadcast")
        return ([], "notify_only")

    # ─── LOW ─────────────────────────────────────────────────────────────────
    if urgency == "low":
        if is_sleeping:
            return ([], "suppressed")
        occupied = currently_occupied_areas(hass)
        if not occupied:
            return ([], "suppressed")
        for area in occupied:
            speakers = [
                s for s in speakers_in_area(hass, area)
                if not s.startswith("assist_satellite.")
            ]
            if speakers:
                return ([speakers[0]], "local")
        return ([], "suppressed")

    return ([], "suppressed")


# ─── Routing: broadcast (briefing / sentinel / doorbell) ─────────────────────

def broadcast_target(
    hass: HomeAssistant,
    *,
    broadcast_group: Optional[str] = None,
) -> list[str]:
    """
    Pick speakers for explicit broadcast announcements (briefing, sentinel alert,
    doorbell, face recognition). These are always to-everyone, not room-routed.

    Priority:
      1. Configured broadcast_group entity (single entity, typically a Cast group)
      2. All media_player entities that are not satellites (aggregate)
      3. Empty list (no audio output)
    """
    if broadcast_group:
        state = hass.states.get(broadcast_group)
        if state is not None:
            return [broadcast_group]

    # Fallback — every speaker that isn't a satellite
    return [
        s for s in _entities_by_domain(hass, "media_player")
        if not s.startswith("assist_satellite.")
    ]


# ─── Bedroom / sleep detection helpers ───────────────────────────────────────

def is_any_bedroom_occupied(
    hass: HomeAssistant, bedroom_area_ids: Iterable[str]
) -> tuple[bool, Optional[str]]:
    """
    Return (occupied, area_id) — True + the area_id of the first bedroom
    currently reporting occupancy, or (False, None).
    """
    for area_id in bedroom_area_ids:
        if is_area_occupied(hass, area_id):
            return True, area_id
    return False, None


def get_bedroom_presence_entities(
    hass: HomeAssistant, bedroom_area_ids: Iterable[str]
) -> list[str]:
    """All occupancy sensors in any bedroom area."""
    out = []
    for area_id in bedroom_area_ids:
        out.extend(presence_entities_in_area(hass, area_id))
    return out
