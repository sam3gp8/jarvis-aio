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
# Modern Frigate (0.14+/HA integration 5.9.2+) publishes recognized faces here
# as {"type": "face", "name": "Sam", "score": 0.93, "camera": "...", ...} and
# exposes a sensor.<camera>_last_recognized_face per camera. This is the
# reliable channel for Frigate-native face recognition — the older sub_label on
# frigate/events isn't always populated. (v6.66.0)
TRACKED_OBJECT_TOPIC = "frigate/tracked_object_update"

# Cache of recent recognitions keyed by camera entity
# {camera_entity: {"name": "Sam", "confidence": 98.7, "ts": datetime, "unknown_count": int}}
_RECOGNITION_CACHE: dict[str, dict] = {}
# Cache of recent Frigate events keyed by camera entity for snapshot retrieval
_RECENT_EVENTS: dict[str, dict] = {}
CACHE_MAX_AGE = timedelta(hours=2)
CONFIDENCE_THRESHOLD = 60  # anything below this is considered uncertain


def _normalize_score(raw) -> float:
    """Frigate scores are 0..1; return a 0..100 percent. Never raises."""
    try:
        if raw is None:
            return 0.0
        v = float(raw)
        return round(v * 100.0 if v <= 1.0 else v, 1)
    except (ValueError, TypeError):
        return 0.0


# Values a last_recognized_face sensor uses to mean "no known person right now"
_FACE_SENSOR_EMPTY = ("none", "unknown", "unavailable", "unknown_face", "")


def read_frigate_face_sensors(hass) -> list[dict]:
    """Read every `sensor.*_last_recognized_face` entity Frigate exposes and
    return the ones currently naming a known person:
    [{camera, camera_entity, name, entity, confidence}]. This is the on-demand
    query path — it answers "who do you see / can you recognize me" from the
    sensor states, complementing the real-time MQTT path. Never raises.

    Note: the sensor holds the LAST recognized face and doesn't reset to none
    the instant a person leaves, so callers should treat this as "most recently
    seen", not "in frame right now". Confidence may live in an attribute
    (score/confidence) when the integration provides it."""
    out = []
    try:
        states = hass.states.async_all("sensor")
    except Exception:
        return out
    for st in states:
        try:
            eid = st.entity_id
            if not eid.endswith("_last_recognized_face"):
                continue
            val = str(st.state or "").strip()
            if val.lower() in _FACE_SENSOR_EMPTY:
                continue
            # derive the camera slug from sensor.<camera>_last_recognized_face
            slug = eid[len("sensor."):-len("_last_recognized_face")]
            attrs = st.attributes or {}
            conf = _normalize_score(
                attrs.get("score", attrs.get("confidence", attrs.get("sub_label_score"))))
            out.append({
                "camera": slug,
                "camera_entity": f"camera.{slug}",
                "name": val,
                "entity": eid,
                "confidence": conf,
            })
        except Exception:
            continue
    return out


def who_do_you_see(hass) -> dict:
    """Answer 'can you recognize me / who do you see' from all available identity
    sources — the recent-recognition cache (populated by MQTT) and the Frigate
    face sensors. Returns {seen: [names], detail: [...], any: bool}. Never
    raises. This is what the agent's identity query should consult so JARVIS can
    say yes when Frigate is naming a face."""
    detail = []
    seen = []

    # 1. Frigate last_recognized_face sensors (on-demand, most reliable here)
    for f in read_frigate_face_sensors(hass):
        if f["name"] and f["name"].lower() not in _FACE_SENSOR_EMPTY:
            detail.append({**f, "source": "frigate_sensor"})
            if f["name"] not in seen:
                seen.append(f["name"])

    # 2. Recent-recognition cache (MQTT-driven, any source), still fresh
    now = datetime.utcnow()
    for cam, rec in _RECOGNITION_CACHE.items():
        name = rec.get("name", "")
        ts = rec.get("ts")
        if not name or name.lower() in _FACE_SENSOR_EMPTY:
            continue
        if ts and (now - ts) > CACHE_MAX_AGE:
            continue
        if name not in seen:
            seen.append(name)
            detail.append({
                "camera_entity": cam, "name": name,
                "confidence": rec.get("confidence", 0.0),
                "source": "recent_cache",
            })

    return {"seen": seen, "detail": detail, "any": bool(seen)}


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
    """One-line summary for the conversation agent's system prompt, merging the
    MQTT-driven recognition cache with Frigate's last_recognized_face sensors —
    so JARVIS can answer "can you see me" from whichever source has data."""
    bits = []
    seen_names = set()

    # MQTT/DoubleTake cache
    current = who_is_where(hass)
    for entity_id, name in (current or {}).items():
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
        seen_names.add(name.lower())

    # Frigate last_recognized_face sensors (may have data before an MQTT event)
    try:
        for f in read_frigate_face_sensors(hass):
            nm = f.get("name", "")
            if nm and nm.lower() not in seen_names:
                friendly_cam = f["camera"].replace("_", " ")
                conf = f.get("confidence", 0)
                tail = f" ~{conf:.0f}%" if conf else ""
                bits.append(f"{nm} recognized at {friendly_cam}{tail}")
                seen_names.add(nm.lower())
    except Exception:
        pass

    if not bits:
        return ""
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

    # Recognition source selection (v6.64.1): 'both' (default), 'doubletake',
    # or 'frigate'. Running both when both are configured causes duplicate
    # recognition events, so let the user pick. This gates only which IDENTITY
    # source fires jarvis_face_recognized — Frigate person *detection* (which
    # triggers camera analysis) runs regardless.
    try:
        from . import jarvis_config
        _rec_source = str(jarvis_config.get("recognition_source", "both") or "both").lower()
    except Exception:
        _rec_source = "both"
    _use_doubletake = _rec_source in ("both", "doubletake")
    _use_frigate_id = _rec_source in ("both", "frigate")

    if _use_doubletake:
        try:
            unsub = await mqtt.async_subscribe(hass, MATCHES_TOPIC, _matches_handler)
            unsubs.append(unsub)
            _LOGGER.info("JARVIS: subscribed to %s for face recognition", MATCHES_TOPIC)
        except Exception as exc:
            _LOGGER.warning("JARVIS: could not subscribe to DoubleTake matches: %s", exc)
    else:
        _LOGGER.info("JARVIS: DoubleTake identity disabled (recognition_source=%s)", _rec_source)

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
                # Gated by recognition_source (v6.64.1): person detection above
                # always runs, but identity firing honors the chosen source.
                if _use_frigate_id:
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

    # ── Frigate-native face recognition via tracked_object_update (v6.66.0) ──
    # Modern Frigate publishes {"type":"face","name":"Sam","score":0.93,...} to
    # frigate/tracked_object_update. This is the RELIABLE identity channel — the
    # sub_label on frigate/events isn't always populated, which is why JARVIS
    # couldn't previously "see" a known person. Gated by recognition_source.
    if _use_frigate_id:
        try:
            async def _tracked_update_handler(msg):
                try:
                    payload = json.loads(msg.payload)
                    if payload.get("type") != "face":
                        return
                    name = str(payload.get("name") or "").strip()
                    if not name or name.lower() in ("none", "null"):
                        return
                    camera = str(payload.get("camera") or "").strip()
                    conf = _normalize_score(payload.get("score"))
                    is_unknown = name.lower() in ("unknown", "unknown person")
                    if camera:
                        remember_recognition(camera, name, conf)
                    hass.bus.async_fire("jarvis_face_recognized", {
                        "camera": camera,
                        "camera_entity": f"camera.{camera}" if camera else "",
                        "name": name,
                        "confidence": conf,
                        "is_unknown": is_unknown,
                        "is_confident": conf >= CONFIDENCE_THRESHOLD,
                        "source": "frigate_tracked_object",
                    })
                    if not is_unknown:
                        _LOGGER.info(
                            "JARVIS: Frigate recognized %s @ %s (%.1f%%) via tracked_object_update",
                            name, camera or "?", conf,
                        )
                except Exception as exc:
                    _LOGGER.debug("Frigate tracked_object_update parse error: %s", exc)

            unsub_tou = await mqtt.async_subscribe(
                hass, TRACKED_OBJECT_TOPIC, _tracked_update_handler
            )
            unsubs.append(unsub_tou)
            _LOGGER.info("JARVIS: subscribed to %s for Frigate face recognition",
                         TRACKED_OBJECT_TOPIC)
        except Exception as exc:
            _LOGGER.debug("JARVIS: Frigate tracked_object_update subscription skipped: %s", exc)

    return unsubs
