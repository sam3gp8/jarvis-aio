"""
JARVIS — Facial recognition awareness (Frigate-native, DoubleTake, CompreFace).

Two independent identity sources, either or both:

1. Frigate-native (v6.59.0): when Frigate's own face recognition (or a plus
   model) attaches a `sub_label` to a person event on frigate/events, JARVIS
   reads it directly — no Double Take needed. This is the preferred path: one
   fewer add-on, identity straight from the detection stream JARVIS already
   watches.

2. DoubleTake (optional): publishes MQTT messages to double-take/matches:
     {"id": "<id>", "camera": "front_door",
      "match": {"name": "Sam", "confidence": 98.7, ...}, ...}
   and creates HA sensors sensor.double_take_<name>. Still supported for setups
   that use it.

Both sources converge on the same result: they cache recent matches per camera
(for JARVIS context) and fire a jarvis_face_recognized event on the bus (for
automations), so downstream persona/greeting logic doesn't care which produced
the identity. Helpers answer 'who was last seen at the front door?' in chat.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from homeassistant.core import HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

MATCHES_TOPIC = "double-take/matches"
CAMERAS_TOPIC = "double-take/cameras"

# Cache of recent recognitions keyed by camera entity
# {camera_entity: {"name": "Sam", "confidence": 98.7, "ts": datetime, "unknown_count": int}}
_RECOGNITION_CACHE: dict[str, dict] = {}
# Cache of recent Frigate events keyed by camera entity for snapshot retrieval
_RECENT_EVENTS: dict[str, dict] = {}
CACHE_MAX_AGE = timedelta(hours=2)
CONFIDENCE_THRESHOLD = 60  # anything below this is considered uncertain


def _parse_sub_label(sub) -> tuple[str, float]:
    """Normalize Frigate's sub_label into (name, confidence_percent).

    Frigate represents a recognized face sub-label differently across versions:
      - a bare string:            "Sam"
      - a [name, score] pair:     ["Sam", 0.92]   (score 0..1)
    Returns ("", 0.0) when there's no usable name. Never raises.
    """
    try:
        if not sub:
            return "", 0.0
        if isinstance(sub, str):
            return sub.strip(), 0.0
        if isinstance(sub, (list, tuple)) and sub:
            name = str(sub[0] or "").strip()
            conf = 0.0
            if len(sub) > 1 and sub[1] is not None:
                raw = float(sub[1])
                conf = raw * 100.0 if raw <= 1.0 else raw   # 0..1 → percent
            return name, round(conf, 1)
    except (ValueError, TypeError):
        pass
    return "", 0.0


def _camera_entity_from_name(camera_name: str) -> str:
    """DoubleTake uses Frigate's camera name ('front_door'); HA entity is camera.front_door."""
    return f"camera.{camera_name.lower()}"


def remember_recognition(camera_name: str, name: str, confidence: float) -> None:
    """Store a recognition event in the in-memory cache."""
    entity_id = _camera_entity_from_name(camera_name)
    now = datetime.utcnow()
    prev = _RECOGNITION_CACHE.get(entity_id, {})
    _RECOGNITION_CACHE[entity_id] = {
        "name":       name,
        "confidence": confidence,
        "ts":         now,
        "unknown_count": 0 if name.lower() != "unknown" else prev.get("unknown_count", 0) + 1,
    }


def last_seen_at(hass: HomeAssistant, camera_entity: str) -> Optional[dict]:
    """Return most recent recognition on that camera, or None if stale."""
    rec = _RECOGNITION_CACHE.get(camera_entity)
    if not rec:
        return None
    age = datetime.utcnow() - rec["ts"]
    if age > CACHE_MAX_AGE:
        return None
    return {
        **rec,
        "age_seconds": int(age.total_seconds()),
        "camera_entity": camera_entity,
    }


def who_is_where(hass: HomeAssistant) -> dict[str, str]:
    """Return {camera_entity: name} for all recent recognitions."""
    out = {}
    cutoff = datetime.utcnow() - CACHE_MAX_AGE
    for entity_id, rec in _RECOGNITION_CACHE.items():
        if rec["ts"] >= cutoff and rec["confidence"] >= CONFIDENCE_THRESHOLD:
            out[entity_id] = rec["name"]
    return out


def recognition_context_string(hass: HomeAssistant) -> str:
    """One-line summary for the conversation agent's system prompt."""
    current = who_is_where(hass)
    if not current:
        return ""

    bits = []
    for entity_id, name in current.items():
        rec = _RECOGNITION_CACHE[entity_id]
        age = int((datetime.utcnow() - rec["ts"]).total_seconds())
        if age < 60:
            when = "just now"
        elif age < 3600:
            when = f"{age // 60}m ago"
        else:
            when = f"{age // 3600}h ago"
        friendly_cam = entity_id.replace("camera.", "").replace("_", " ")
        bits.append(f"{name} seen at {friendly_cam} ({when})")
    return "Recent faces: " + "; ".join(bits) + "."


# ─── MQTT subscription ───────────────────────────────────────────────────────

async def register_recognition_listener(hass: HomeAssistant) -> list:
    """
    Subscribe to DoubleTake's MQTT topic.
    Returns list of unsub callables. Empty list if MQTT isn't configured.
    """
    unsubs = []
    try:
        from homeassistant.components import mqtt
    except ImportError:
        _LOGGER.info("JARVIS: MQTT component not available — face recognition listener skipped")
        return unsubs

    # Check that MQTT is actually set up
    if not hass.services.has_service("mqtt", "publish"):
        _LOGGER.info("JARVIS: MQTT not configured — face recognition listener skipped")
        return unsubs

    @callback
    def _matches_handler(msg):
        try:
            payload = json.loads(msg.payload) if isinstance(msg.payload, (str, bytes)) else msg.payload
        except (json.JSONDecodeError, TypeError):
            return

        camera = payload.get("camera") or payload.get("camera_name")
        match = payload.get("match") or {}
        if not camera or not match:
            return

        name = match.get("name", "unknown")
        confidence = float(match.get("confidence", 0))

        remember_recognition(camera, name, confidence)

        # Fire a custom event that automations/blueprints can use
        hass.bus.async_fire(
            "jarvis_face_recognized",
            {
                "camera":      camera,
                "camera_entity": _camera_entity_from_name(camera),
                "name":        name,
                "confidence":  confidence,
                "is_unknown":  name.lower() == "unknown",
                "is_confident": confidence >= CONFIDENCE_THRESHOLD,
            },
        )
        _LOGGER.info("JARVIS: face recognized — %s @ %s (%.1f%%)", name, camera, confidence)

    try:
        unsub = await mqtt.async_subscribe(hass, MATCHES_TOPIC, _matches_handler)
        unsubs.append(unsub)
        _LOGGER.info("JARVIS: subscribed to %s for face recognition", MATCHES_TOPIC)
    except Exception as exc:
        _LOGGER.warning("JARVIS: could not subscribe to DoubleTake matches: %s", exc)

    # Frigate person detection via MQTT
    FRIGATE_EVENTS_TOPIC = "frigate/events"
    try:
        async def _frigate_handler(msg):
            """Handle Frigate MQTT events — trigger analysis on person detection."""
            try:
                payload = json.loads(msg.payload)
                event_type = payload.get("type")
                after = payload.get("after", {})
                label = after.get("label", "")
                camera = after.get("camera", "")
                score = after.get("top_score", 0)

                # Only act on new person detections with high confidence
                if event_type != "new" or label != "person" or score < 0.7:
                    return

                camera_entity = f"camera.{camera}"
                event_id = after.get("id", "")

                _LOGGER.info(
                    "JARVIS: Frigate person detected on %s (score=%.1f%%, event=%s)",
                    camera, score * 100, event_id[:8],
                )

                # Cache the event for snapshot retrieval
                _RECENT_EVENTS[camera_entity] = {
                    "event_id": event_id,
                    "source": "frigate",
                    "ts": datetime.now(),
                }

                # Fire HA event for blueprints/automations
                hass.bus.async_fire("jarvis_person_detected", {
                    "camera": camera,
                    "camera_entity": camera_entity,
                    "label": label,
                    "score": score,
                    "event_id": event_id,
                })

                # ── Frigate-native identity (v6.59.0) ──────────────────────
                # If Frigate's own face recognition (or a +/- plus model)
                # attached a sub_label, use it directly — no Double Take needed.
                # sub_label is either "Name" or ["Name", score] across versions.
                sub = after.get("sub_label")
                sub_name, sub_conf = _parse_sub_label(sub)
                if sub_name:
                    remember_recognition(camera, sub_name, sub_conf)
                    hass.bus.async_fire("jarvis_face_recognized", {
                        "camera": camera,
                        "camera_entity": camera_entity,
                        "name": sub_name,
                        "confidence": sub_conf,
                        "is_unknown": sub_name.lower() in ("unknown", "unknown person"),
                        "is_confident": sub_conf >= CONFIDENCE_THRESHOLD,
                        "source": "frigate",
                    })
                    _LOGGER.info(
                        "JARVIS: Frigate identified %s @ %s (%.1f%%) via sub_label",
                        sub_name, camera, sub_conf,
                    )

            except Exception as exc:
                _LOGGER.debug("Frigate event parse error: %s", exc)

        unsub_frigate = await mqtt.async_subscribe(
            hass, FRIGATE_EVENTS_TOPIC, _frigate_handler
        )
        unsubs.append(unsub_frigate)
        _LOGGER.info("JARVIS: subscribed to %s for Frigate person detection", FRIGATE_EVENTS_TOPIC)
    except Exception as exc:
        _LOGGER.debug("JARVIS: Frigate MQTT subscription skipped: %s", exc)

    return unsubs
