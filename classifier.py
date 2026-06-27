"""
JARVIS — Observer Tier 1: the classifier (v5.7.00).

Rule-based classifier handles 95%+ of events with zero API calls.
Only genuinely ambiguous events fall through to LLM classification.

Previous versions sent every event to Groq (~500 calls/day).
Now: Python rules handle obvious cases. LLM fallback for <20/day.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

_LOGGER = logging.getLogger(__name__)

from .const import URGENCY_CEILINGS

# Entity prefixes that are always noise
_NOISE_PREFIXES = (
    "sensor.sun_", "sensor.time", "sensor.date", "sensor.uptime",
    "sensor.last_boot", "sensor.cpu", "sensor.memory", "sensor.disk",
    "sensor.network", "sensor.processor", "sensor.load_", "sensor.swap",
    "button.", "number.", "input_number.", "input_select.",
    "input_boolean.", "input_text.", "input_datetime.",
    "update.", "automation.", "script.", "scene.", "zone.",
    "device_tracker.", "persistent_notification.", "counter.", "timer.",
)

_NOISE_DEVICE_CLASSES = {
    "connectivity", "plug", "power", "update", "running",
    "battery_charging",
}

_NOISE_TRANSITIONS = {
    ("unavailable", "unknown"), ("unknown", "unavailable"),
    ("unavailable", "unavailable"), ("unknown", "unknown"),
}


def _rule_classify(
    entity_id: str, old_state: str, new_state: str,
    friendly_name: str, device_class: Optional[str], now_hhmm: str,
) -> Optional[dict]:
    """Rule-based classification. Returns dict or None (ambiguous → LLM)."""
    eid = entity_id.lower()
    dc = (device_class or "").lower()

    # Noise prefixes
    for p in _NOISE_PREFIXES:
        if eid.startswith(p):
            return {"worth_considering": False, "rule": "noise_prefix"}

    # Junk transitions
    if (old_state, new_state) in _NOISE_TRANSITIONS or old_state == new_state:
        return {"worth_considering": False, "rule": "noise_transition"}

    # Noise device classes
    if dc in _NOISE_DEVICE_CLASSES:
        return {"worth_considering": False, "rule": "noise_dc"}

    # Safety-critical
    ceiling = URGENCY_CEILINGS.get(dc)
    if ceiling == "critical":
        return {"worth_considering": True, "urgency": "critical",
                "category": "security", "rule": "safety"}

    # Doors/windows/garage
    if dc in ("door", "window", "garage_door"):
        if new_state == "on":
            try:
                h = int(now_hhmm.split(":")[0])
                urg = "high" if (h >= 23 or h < 6) else "medium"
            except Exception:
                urg = "medium"
            return {"worth_considering": True, "urgency": urg,
                    "category": "doors_windows", "rule": "door_opened"}
        return {"worth_considering": False, "rule": "door_closed"}

    # Motion/occupancy
    if dc in ("motion", "occupancy"):
        if new_state == "off":
            return {"worth_considering": False, "rule": "motion_clear"}
        try:
            h = int(now_hhmm.split(":")[0])
            if h >= 23 or h < 5:
                return {"worth_considering": True, "urgency": "low",
                        "category": "presence", "rule": "motion_night"}
        except Exception:
            pass
        return {"worth_considering": False, "rule": "motion_routine"}

    # Person entities
    if eid.startswith("person."):
        if new_state == "home" and old_state != "home":
            return {"worth_considering": True, "urgency": "medium",
                    "category": "presence", "rule": "arrived"}
        if old_state == "home" and new_state != "home":
            return {"worth_considering": True, "urgency": "medium",
                    "category": "presence", "rule": "left"}
        return {"worth_considering": False, "rule": "person_same"}

    # Locks
    if eid.startswith("lock."):
        if new_state == "unlocked":
            return {"worth_considering": True, "urgency": "medium",
                    "category": "security", "rule": "unlocked"}
        return {"worth_considering": False, "rule": "locked"}

    # Alarm
    if eid.startswith("alarm_control_panel."):
        if new_state in ("triggered", "pending"):
            return {"worth_considering": True, "urgency": "critical",
                    "category": "security", "rule": "alarm"}
        return {"worth_considering": True, "urgency": "low",
                "category": "security", "rule": "alarm_change"}

    # Lights, switches, climate, media — routine
    for prefix in ("light.", "switch.", "climate.", "media_player."):
        if eid.startswith(prefix):
            return {"worth_considering": False, "rule": f"{prefix[:-1]}_routine"}

    # Sensors — flag large power spikes only
    if eid.startswith("sensor."):
        try:
            delta = abs(float(new_state) - float(old_state))
            if ("power" in eid or "watt" in eid) and delta > 1000:
                return {"worth_considering": True, "urgency": "low",
                        "category": "energy", "rule": "power_spike"}
        except (ValueError, TypeError):
            pass
        return {"worth_considering": False, "rule": "sensor_drift"}

    # Binary sensors without device class
    if eid.startswith("binary_sensor.") and not dc:
        return {"worth_considering": False, "rule": "binary_no_dc"}

    # Ambiguous — let LLM decide
    return None


# LLM fallback prompt (kept short to minimize tokens)
_LLM_PROMPT = (
    "Classify this Home Assistant event. Return ONLY JSON:\n"
    '{"worth_considering": bool, "urgency": "low|medium|high|critical", "category": "..."}\n'
    "Categories: appliances, doors_windows, presence, energy, security, climate, other"
)


def _parse_json(raw: str) -> dict:
    if not raw:
        return {"worth_considering": False}
    raw = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if m:
        raw = m.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m2 = re.search(r"\{[^}]+\}", raw)
        if m2:
            try:
                return json.loads(m2.group(0))
            except json.JSONDecodeError:
                pass
    return {"worth_considering": False}


async def classify(
    hass, provider, *, entity_id: str, old_state: str, new_state: str,
    friendly_name: str = "", device_class: Optional[str] = None,
    now_hhmm: str = "", recent_activity_summary: str = "",
) -> dict:
    """Classify event. Rules first, LLM fallback for ambiguous."""
    result = _rule_classify(entity_id, old_state, new_state,
                            friendly_name, device_class, now_hhmm)
    if result is not None:
        return result

    # Ambiguous — LLM (rare)
    _LOGGER.info("Classifier LLM for: %s (%s→%s)", entity_id, old_state, new_state)
    try:
        from .websocket import jarvis_log
        jarvis_log("CLASSIFY", f"LLM needed: {entity_id} ({old_state}→{new_state})")
    except Exception:
        pass
    msg = f"Time:{now_hhmm} Entity:{entity_id} Name:{friendly_name} Class:{device_class} {old_state}→{new_state}"
    try:
        resp = await hass.async_add_executor_job(
            lambda: provider.chat(
                [{"role": "system", "content": _LLM_PROMPT},
                 {"role": "user", "content": msg}],
                temperature=0.0, max_tokens=80,
            )
        )
        parsed = _parse_json(resp.get("text", "") if isinstance(resp, dict) else str(resp))
        if not parsed.get("worth_considering"):
            return {"worth_considering": False}
        urg = parsed.get("urgency", "low")
        if urg not in ("low", "medium", "high", "critical"):
            urg = "low"
        return {"worth_considering": True, "urgency": urg,
                "category": parsed.get("category", "other")}
    except Exception as exc:
        _LOGGER.warning("Classifier LLM failed for %s: %s", entity_id, exc)
        return {"worth_considering": False}
