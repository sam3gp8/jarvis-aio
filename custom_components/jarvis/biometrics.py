"""
JARVIS biometric & wellbeing context (v6.63.0).

Lets JARVIS "feel" the user's state by reading biometric entities a wearable
already surfaces to Home Assistant (heart rate, sleep stage, steps, etc.) — so
it can be quieter when you're resting, and factor wellbeing into how it
behaves.

**This is NOT a medical device and never behaves like one.** It reads existing
HA entities as *comfort and context* signals only. It does not diagnose, does
not raise health alarms, and does not interpret vitals clinically. If a reading
looks concerning, JARVIS's job is to gently suggest the person check with a real
medical resource or their own device — never to render a verdict. All framing
here is deliberately non-clinical, and the surfaced context is advisory context
for JARVIS's *own behavior* (e.g. suppress chatter when a sleep sensor says
asleep), not health guidance for the user.

Discovery is heuristic over entity/device-class/unit, so it works across
wearables (Withings, Google Fit, Apple Health bridges, Oura, Fitbit, etc.)
without hard-coding any one integration. Nothing here raises to the caller.
"""
from __future__ import annotations

import logging
from typing import Optional

_LOGGER = logging.getLogger(__name__)

_UNKNOWN = ("unknown", "unavailable", "none", "")

# Heuristic matchers for each biometric kind: (keywords, unit hints).
# Matched against entity_id + friendly_name (lowercased) and unit_of_measurement.
_KINDS = {
    "heart_rate":    (("heart_rate", "heartrate", "heart rate", "_hr", "bpm", "pulse"),
                      ("bpm",)),
    "resting_hr":    (("resting_heart", "resting hr", "rest_heart"), ("bpm",)),
    "sleep_stage":   (("sleep_stage", "sleep state", "sleep_status", "sleep phase"),
                      ()),
    "sleep_state":   (("is_asleep", "asleep", "in_bed", "sleep", "sleeping"), ()),
    "spo2":          (("spo2", "blood_oxygen", "oxygen_saturation", "sp_o2"),
                      ("%",)),
    "steps":         (("steps", "step_count", "step count"), ("steps",)),
    "respiratory":   (("respiratory", "breathing_rate", "resp_rate"),
                      ("br",)),
    "stress":        (("stress", "stress_level"), ()),
    "temperature":   (("body_temp", "skin_temp", "body temperature"), ()),
}

# Kinds that indicate rest/sleep — used to strengthen the sleep signal.
_SLEEP_KINDS = ("sleep_stage", "sleep_state")
# Values (lowercased, substring) that mean "asleep" on a sleep entity.
_ASLEEP_VALUES = ("asleep", "sleeping", "deep", "rem", "light_sleep", "light sleep",
                  "in_bed", "on")


def _cfg(key: str, default=None):
    try:
        from . import jarvis_config
        v = jarvis_config.get(key, default)
        return v if v is not None else default
    except Exception:
        return default


def is_enabled() -> bool:
    """Biometric context is opt-in (health data — off unless the user turns it on)."""
    return bool(_cfg("biometrics_enabled", False))


# ── discovery ────────────────────────────────────────────────────────────────

def _classify(entity_id: str, name: str, unit: str, device_class: str) -> Optional[str]:
    hay = f"{entity_id} {name}".lower()
    unit = (unit or "").lower()
    # explicit user mapping wins
    mapping = _cfg("biometric_entities", {})
    if isinstance(mapping, dict):
        for kind, eid in mapping.items():
            if eid == entity_id:
                return kind
    for kind, (kws, units) in _KINDS.items():
        if any(k in hay for k in kws):
            return kind
        if units and unit in units:
            return kind
    return None


def discover(hass) -> dict:
    """Find biometric entities on the system, grouped by kind. Returns
    {kind: [{entity, value, unit, name}]}. Never raises."""
    found: dict[str, list] = {}
    try:
        states = hass.states.async_all("sensor") + hass.states.async_all("binary_sensor")
    except Exception:
        return found
    for st in states:
        try:
            eid = st.entity_id
            name = st.attributes.get("friendly_name", "") or ""
            unit = st.attributes.get("unit_of_measurement", "") or ""
            dc = st.attributes.get("device_class", "") or ""
            kind = _classify(eid, name, unit, dc)
            if not kind:
                continue
            found.setdefault(kind, []).append({
                "entity": eid, "value": st.state, "unit": unit, "name": name,
            })
        except Exception:
            continue
    return found


# ── context signals (for JARVIS's own behavior — non-medical) ────────────────

def sleep_signal(hass) -> Optional[bool]:
    """If a wearable exposes a sleep entity, return True/False for asleep, else
    None (no signal). This can STRENGTHEN sleep_detection's occupancy heuristic —
    a wearable saying 'asleep' is stronger evidence than bedroom occupancy."""
    if not is_enabled():
        return None
    bio = discover(hass)
    for kind in _SLEEP_KINDS:
        for ent in bio.get(kind, []):
            val = str(ent.get("value", "")).strip().lower()
            if val in _UNKNOWN:
                continue
            if any(a in val for a in _ASLEEP_VALUES):
                return True
            if val in ("awake", "off", "not_asleep", "out_of_bed"):
                return False
    return None


def wellbeing_context(hass) -> dict:
    """A compact, non-clinical snapshot for JARVIS's context — what a wearable
    reports, phrased as ambient context, never as a health assessment. Returns
    {available, summary, readings}. Never raises and never diagnoses."""
    if not is_enabled():
        return {"available": False, "summary": "", "readings": {}}
    bio = discover(hass)
    if not bio:
        return {"available": False,
                "summary": "no biometric entities found — connect a wearable "
                           "integration to Home Assistant",
                "readings": {}}

    readings = {}
    for kind, ents in bio.items():
        # take the first usable reading per kind
        for e in ents:
            v = str(e.get("value", "")).strip()
            if v.lower() not in _UNKNOWN:
                readings[kind] = {"value": v, "unit": e.get("unit", "")}
                break

    # A purely descriptive summary — no interpretation, no thresholds, no alarms.
    bits = []
    if "heart_rate" in readings:
        bits.append(f"heart rate {readings['heart_rate']['value']} bpm")
    if "sleep_stage" in readings:
        bits.append(f"sleep: {readings['sleep_stage']['value']}")
    elif "sleep_state" in readings:
        bits.append(f"sleep state: {readings['sleep_state']['value']}")
    if "steps" in readings:
        bits.append(f"{readings['steps']['value']} steps today")
    summary = "; ".join(bits) if bits else "biometric data present"

    return {
        "available": True,
        "summary": summary,
        "readings": readings,
        # Explicit reminder carried in the payload so any consumer stays honest.
        "disclaimer": "context only — not medical advice or assessment",
    }
