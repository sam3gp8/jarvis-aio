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


def get_presence_summary(hass: HomeAssistant) -> dict:
    """
    Return a summary of who's home and where.

    Shape:
      {
        "total_people":  int,
        "home_count":    int,
        "away_count":    int,
        "people":        [{"name": "Sam", "state": "home", ...}, ...],
        "rooms":         {"kitchen": ["Sam"], "office": ["Alex"]},
        "anyone_home":   bool,
      }
    """
    people = []
    for state in hass.states.async_all("person"):
        info = _person_state(hass, state.entity_id)
        if info:
            people.append(info)

    home_count = sum(1 for p in people if p.get("state") == "home")
    away_count = len(people) - home_count

    # Room detection via mmWave sensors — Aqara FP2 exposes sensor.*_presence
    # with occupancy attributes, or binary_sensor.*_occupancy. We scan both.
    rooms: dict[str, list[str]] = {}
    try:
        from .entity_filter import is_excluded as _excl
    except Exception:
        _excl = lambda _h, _e: False
    try:
        from .entity_filter import is_nonperson_object_sensor as _nonperson
    except Exception:
        _nonperson = lambda _e, _n=None: False
    for state in hass.states.async_all("binary_sensor"):
        if state.state != "on":
            continue
        if state.attributes.get("device_class") != "occupancy":
            continue
        if _excl(hass, state.entity_id):
            continue  # user excluded this sensor (e.g. a virtual occupancy sensor)
        name = state.attributes.get("friendly_name", state.entity_id)
        if _nonperson(state.entity_id, name):
            continue  # #254: a car/animal/package detector is not a human being present

        # Try to extract the room from the name (e.g. "Kitchen Presence")
        room = name.lower().replace("presence", "").replace("occupancy", "").strip()
        if room:
            rooms.setdefault(room, [])

    if CONFLICT_SHADOW:
        _emit_presence_conflict_shadow(hass)   # #237: observe-only

    return {
        "total_people": len(people),
        "home_count":   home_count,
        "away_count":   away_count,
        "people":       people,
        "rooms":        rooms,
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
        summary += f" Occupied areas: {', '.join(data['rooms'].keys())}."
    return summary


def find_person(hass: HomeAssistant, name: str) -> Optional[dict]:
    """Find a person entity by friendly name (case-insensitive, partial match OK)."""
    needle = name.lower().strip()
    for state in hass.states.async_all("person"):
        friendly = state.attributes.get("friendly_name", "").lower()
        if needle in friendly or needle in state.entity_id.lower():
            return _person_state(hass, state.entity_id)
    return None
