"""
JARVIS — Presence awareness.

Reads HA's person.* entities, zone states, and (when available) mmWave room
sensors to know who is home and where. Provides a concise summary string for
the conversation agent's context.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Optional

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# SHADOW (#237, Epistemic Fabric — Conflict): get_presence_summary() also
# adjudicates each person's own backing device_trackers through kernel.conflict —
# one kernel.provenance.Provenance per tracker (value = its home/away/zone state,
# source = the tracker entity, reliability by source_type, observed-at from the
# state's last_updated) — and logs the resolved winner, whether it is CONTESTED
# (trackers disagree within the margin) and whether it AGREES with HA's own
# person.state. This is the primitive's canonical case: the phone says home, the
# watch says away. Observe-only: HA's own resolution still stands and the summary
# is unchanged; a consumer reads the adjudicated value authoritatively only at
# enforce (owner-gated). Flip CONFLICT_SHADOW to False to silence it.
CONFLICT_SHADOW = True

# PARITY (Epistemic Fabric — Conflict): over a rolling window of the per-person
# adjudications the shadow computes, log how often kernel.conflict's
# reliability-weighted winner AGREES with HA's native person.state and how often
# it would DEFER (CONTESTED). That is the quantified bar the owner-gated enforce
# rung needs — a consumer should read the adjudicated value only once it tracks
# HA closely and rarely defers. Rides inside the shadow emission (so it shares
# the same real reads); observe-only, drives nothing. Flip CONFLICT_PARITY off
# to silence it.
CONFLICT_PARITY = True
_CONFLICT_PARITY_WINDOW = 200
_conflict_verdicts: deque = deque(maxlen=_CONFLICT_PARITY_WINDOW)

# SHADOW (Epistemic Fabric — Conflict, multi-source fusion): get_presence_summary()
# also logs the world-model fusion of each person's whereabouts across independent
# source *types* — HA's own person.state vs a recent camera recognition — via
# WorldModel.fuse_presence (kernel.conflict over kernel.provenance records). This
# broadens the conflict adjudication from the per-device_tracker shadow above to
# whole source types, and is the multi-source verdict the Conflict enforce rung
# will read. Observe-only: HA's resolution still stands. Flip off to silence it.
PRESENCE_FUSION_SHADOW = True

# Trust prior by device_tracker source_type: GPS is strongest, a router/ping
# weaker, a BLE/beacon weaker still. Unknown sources get a neutral prior. These
# feed kernel.conflict's reliability term; the tracker's own assertion is taken
# at confidence 1.0 (it definitively reports a state), so trust lives here.
_TRACKER_RELIABILITY = {
    "gps": 0.9, "router": 0.7, "bluetooth": 0.6, "bluetooth_le": 0.55,
}
_DEFAULT_TRACKER_RELIABILITY = 0.5


def _norm_presence(state) -> Optional[str]:
    """Canonicalize a person/tracker state for conflict grouping: 'home', 'away'
    (from not_home/away), or a lower-cased zone name; None for unknown/
    unavailable so it contributes no vote."""
    s = str(state or "").strip().lower()
    if s in ("", "unknown", "unavailable", "none"):
        return None
    if s == "not_home":
        return "away"
    return s


def _emit_presence_conflict_shadow(hass: HomeAssistant) -> None:
    """For each person with ≥2 backing device_trackers, adjudicate the trackers
    through kernel.conflict and log the winner / contested / agreement with HA's
    own person.state (shadow). Best-effort, never raises — the presence summary
    is unaffected either way."""
    try:
        import time as _t
        from .kernel import conflict as _conflict
        from .kernel import provenance as _prov
        now = _t.time()
        for pstate in hass.states.async_all("person"):
            try:
                trackers = pstate.attributes.get("device_trackers") or []
                if len(trackers) < 2:
                    continue  # no possible conflict with fewer than two sources
                records = []
                reliabilities = {}
                for tid in trackers:
                    tstate = hass.states.get(tid)
                    if not tstate:
                        continue
                    val = _norm_presence(tstate.state)
                    if val is None:
                        continue
                    stype = str(tstate.attributes.get("source_type", "")).lower()
                    reliabilities[tid] = _TRACKER_RELIABILITY.get(
                        stype, _DEFAULT_TRACKER_RELIABILITY)
                    try:
                        observed = tstate.last_updated.timestamp()
                    except Exception:
                        observed = now
                    records.append(_prov.record(
                        val, source=tid, confidence=1.0, now=(lambda o=observed: o)))
                if len(records) < 2:
                    continue
                res = _conflict.resolve(records, reliabilities=reliabilities, now=now)
                ha_state = _norm_presence(pstate.state)
                verdict = ("CONTESTED" if res.contested
                           else "AGREEMENT" if (res.resolved and res.value == ha_state)
                           else "DIVERGENCE" if res.resolved
                           else "—")
                _LOGGER.debug(
                    "presence_conflict(shadow): %s kernel=%r ha=%r resolved=%s %s",
                    pstate.entity_id, res.value, ha_state, res.resolved, verdict)
                # PARITY: accumulate this real adjudication's verdict.
                if CONFLICT_PARITY and verdict != "—":
                    _conflict_verdicts.append(verdict)
            except Exception:
                continue
        _emit_presence_conflict_parity()
    except Exception:   # pragma: no cover - defensive
        pass


def _emit_presence_fusion_shadow(hass: HomeAssistant) -> None:
    """Log the world-model multi-source presence fusion per person — HA's own
    person.state vs a recent camera recognition, adjudicated through
    WorldModel.fuse_presence (kernel.conflict). Observe-only, best-effort, never
    raises — the presence summary is unaffected. Only a genuine ≥2-source case
    (a resolved or contested fusion) is logged."""
    if not PRESENCE_FUSION_SHADOW:
        return
    try:
        from .kernel.world_model import WorldModel
        wm = WorldModel(hass)
        for pstate in hass.states.async_all("person"):
            try:
                name = (pstate.attributes.get("friendly_name")
                        or pstate.entity_id.split(".", 1)[-1])
                res = wm.fuse_presence(name)
                if res.resolved or res.contested:
                    verdict = "CONTESTED" if res.contested else "resolved"
                    _LOGGER.debug(
                        "presence_fusion(shadow): %s kernel=%r %s ranked=%s",
                        pstate.entity_id, res.value, verdict,
                        [[v, round(s, 3)] for v, s in res.ranked])
            except Exception:
                continue
    except Exception:   # pragma: no cover - defensive
        pass


def _emit_presence_conflict_parity() -> None:
    """PARITY (Epistemic Fabric — Conflict): log the accumulated agreement and
    contested rates of kernel.conflict's winner vs HA's native person.state over
    the rolling window. Best-effort, never raises; reads the window, drives
    nothing."""
    if not CONFLICT_PARITY:
        return
    try:
        n = len(_conflict_verdicts)
        if n == 0:
            return
        agree = sum(1 for v in _conflict_verdicts if v == "AGREEMENT")
        diverge = sum(1 for v in _conflict_verdicts if v == "DIVERGENCE")
        contested = sum(1 for v in _conflict_verdicts if v == "CONTESTED")
        decided = agree + diverge            # non-contested resolutions
        agree_rate = (agree / decided) if decided else 0.0
        contested_rate = contested / n
        _LOGGER.debug(
            "presence_conflict(parity): agree_rate=%.2f (%d/%d) "
            "contested_rate=%.2f n=%d", agree_rate, agree, decided,
            contested_rate, n)
    except Exception:   # pragma: no cover - defensive
        pass


def _person_state(hass: HomeAssistant, entity_id: str) -> dict:
    """Extract useful data from a person.* entity."""
    state = hass.states.get(entity_id)
    if not state:
        return {}
    return {
        "name":  state.attributes.get("friendly_name", entity_id.split(".", 1)[-1]),
        "state": state.state,  # 'home', 'not_home', or a zone name
        "latitude":  state.attributes.get("latitude"),
        "longitude": state.attributes.get("longitude"),
    }


def everyone_confidently_away(hass) -> bool:
    """True only when there are tracked people and EVERY one is explicitly away
    (not_home/away). Unknown or unavailable presence returns False — fail open,
    so actions like scheduled briefings are only suppressed when we are sure the
    house is empty, not merely because presence is uncertain (v6.95.0)."""
    try:
        summ = get_presence_summary(hass)
        people = summ.get("people", [])
        if not people or summ.get("home_count", 0) > 0:
            return False
        return all(str(p.get("state", "")).lower() in ("not_home", "away")
                   for p in people)
    except Exception:
        return False


# Device classes that mean "a human is (still) in this area". mmWave/radar
# presence sensors (Shelly Presence Gen4, Aqara FP2, Everything Presence, ESPHome
# LD2410/LD2420, …) report one of these. `motion` is deliberately excluded from
# the awareness summary — it's transient and would make the "who's where" view
# flap — but audio_routing still uses it for immediate room routing.
_PRESENCE_CLASSES = ("occupancy", "presence")

# A radar occupant/target count sensor has no standard device_class, so it's
# recognised by name shape: a numeric "...count" naming a person/target/object.
_COUNT_NAME_TOKENS = ("target", "person", "people", "occupan", "object", "presence")


def _to_number(value) -> Optional[float]:
    """Parse a state string to a float, or None (unknown/unavailable/non-numeric)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def _area_room_name(hass, area_id: Optional[str]) -> Optional[str]:
    """The HA area's human name (lowercased) for an area_id, or None."""
    if not area_id:
        return None
    try:
        from homeassistant.helpers import area_registry as ar
        area = ar.async_get(hass).async_get_area(area_id)
        nm = getattr(area, "name", None) if area else None
        return nm.lower() if nm else None
    except Exception:
        return None


def get_presence_summary(hass: HomeAssistant) -> dict:
    """
    Return a summary of who's home and where.

    Shape:
      {
        "total_people":  int,
        "home_count":    int,
        "away_count":    int,
        "people":        [{"name": "Sam", "state": "home", ...}, ...],
        "rooms":         {"kitchen": [], "living room": []},   # occupied areas
        "room_details":  {"living room": {"occupants": 2, "lux": 40.0}},
        "anyone_home":   bool,
      }

    ``rooms`` keys are occupied areas (mmWave/radar presence sensors, mapped to
    their HA area). ``room_details`` adds occupant count + illuminance per room
    where a radar count / illuminance sensor exists in that area.
    """
    # Respect the user's excluded_entities for people too — entity_filter drops
    # exclusions everywhere JARVIS enumerates entities, presence included, so an
    # excluded person must not be announced as home/away (#581). Hoisted here so
    # the room-detection block below reuses the same _excl.
    try:
        from .entity_filter import is_excluded as _excl
    except Exception:
        _excl = lambda _h, _e: False

    people = []
    _seen_trackers: set = set()
    for state in hass.states.async_all("person"):
        if _excl(hass, state.entity_id):
            continue
        # De-duplicate two person.* entities that track an IDENTICAL set of
        # device_trackers (the same human on the same phone), so they aren't
        # double-counted even without an explicit exclusion (#581).
        _trk = frozenset(state.attributes.get("device_trackers") or [])
        if _trk and _trk in _seen_trackers:
            continue
        info = _person_state(hass, state.entity_id)
        if info:
            people.append(info)
            if _trk:
                _seen_trackers.add(_trk)

    home_count = sum(1 for p in people if p.get("state") == "home")
    away_count = len(people) - home_count

    # Room detection via mmWave/radar presence sensors (Shelly Presence Gen4,
    # Aqara FP2, Everything Presence, ESPHome LD2410, …). Recognise the whole
    # presence family (`occupancy` + `presence`), and map each sensor to the HA
    # AREA its device is assigned to — far more reliable than parsing the sensor's
    # own name, which turns e.g. "Presence Gen4 Occupancy" into a bogus room.
    rooms: dict[str, list[str]] = {}
    room_details: dict[str, dict] = {}
    _area_to_room: dict[str, str] = {}   # area_id -> room, for occupied areas only
    try:
        from .entity_filter import is_nonperson_object_sensor as _nonperson
    except Exception:
        _nonperson = lambda _e, _n=None: False
    try:
        from .audio_routing import entity_area as _entity_area
    except Exception:
        _entity_area = lambda _h, _e: None

    for state in hass.states.async_all("binary_sensor"):
        if state.state != "on":
            continue
        if state.attributes.get("device_class") not in _PRESENCE_CLASSES:
            continue
        if _excl(hass, state.entity_id):
            continue  # user excluded this sensor (e.g. a virtual occupancy sensor)
        name = state.attributes.get("friendly_name", state.entity_id)
        if _nonperson(state.entity_id, name):
            continue  # #254: a car/animal/package detector is not a human being present

        # Prefer the HA area the device is in; fall back to parsing the name.
        area_id = _entity_area(hass, state.entity_id)
        room = _area_room_name(hass, area_id)
        if room:
            if area_id:
                _area_to_room[area_id] = room
        else:
            room = name.lower().replace("presence", "").replace("occupancy", "").strip()
        if room:
            rooms.setdefault(room, [])

    # Occupant COUNT and ILLUMINANCE from radar/light sensors, attributed to an
    # occupied room by HA area — so JARVIS knows "2 people in the living room at
    # 40 lux", not just that the room is occupied. Only for rooms already detected
    # as occupied, so idle sensors elsewhere add no noise.
    if _area_to_room:
        for state in hass.states.async_all("sensor"):
            area_id = _entity_area(hass, state.entity_id)
            room = _area_to_room.get(area_id) if area_id else None
            if not room or _excl(hass, state.entity_id):
                continue
            val = _to_number(state.state)
            if val is None:
                continue
            dc = state.attributes.get("device_class")
            nm = str(state.attributes.get("friendly_name", state.entity_id)).lower()
            det = room_details.setdefault(room, {})
            if dc == "illuminance":
                if det.get("lux") is None or val > det["lux"]:
                    det["lux"] = val
            elif "count" in nm and any(tok in nm for tok in _COUNT_NAME_TOKENS):
                iv = int(val)
                if det.get("occupants") is None or iv > det["occupants"]:
                    det["occupants"] = iv

    if CONFLICT_SHADOW:
        _emit_presence_conflict_shadow(hass)   # #237: observe-only
    if PRESENCE_FUSION_SHADOW:
        _emit_presence_fusion_shadow(hass)     # multi-source fusion: observe-only

    return {
        "total_people": len(people),
        "home_count":   home_count,
        "away_count":   away_count,
        "people":       people,
        "rooms":        rooms,
        "room_details": room_details,   # {room: {"occupants": int, "lux": float}}
        "anyone_home":  home_count > 0,
    }


def presence_context_string(hass: HomeAssistant) -> str:
    """
    One-line summary suitable for injecting into the LLM system prompt.
    Example: "Sam is home. Alex is away. 3 presence sensors active: kitchen, office."
    """
    data = get_presence_summary(hass)
    if not data["people"]:
        return "No person entities configured in Home Assistant."

    bits: list[str] = []
    for p in data["people"]:
        state = p["state"]
        if state == "home":
            bits.append(f"{p['name']} is home")
        elif state == "not_home":
            bits.append(f"{p['name']} is away")
        else:
            # A zone name like 'Work' or 'School'
            bits.append(f"{p['name']} is at {state.replace('_', ' ')}")

    summary = ". ".join(bits) + "."
    if data["rooms"]:
        details = data.get("room_details", {})
        parts: list[str] = []
        for room in data["rooms"].keys():
            d = details.get(room) or {}
            extra: list[str] = []
            occ = d.get("occupants")
            if occ:
                extra.append(f"{occ} {'person' if occ == 1 else 'people'}")
            lux = d.get("lux")
            if lux is not None:
                extra.append(f"{round(lux)} lux")
            parts.append(f"{room} ({', '.join(extra)})" if extra else room)
        summary += f" Occupied areas: {', '.join(parts)}."
    return summary


def find_person(hass: HomeAssistant, name: str) -> Optional[dict]:
    """Find a person entity by friendly name (case-insensitive, partial match OK)."""
    needle = name.lower().strip()
    for state in hass.states.async_all("person"):
        friendly = state.attributes.get("friendly_name", "").lower()
        if needle in friendly or needle in state.entity_id.lower():
            return _person_state(hass, state.entity_id)
    return None
