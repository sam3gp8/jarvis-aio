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
import os
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Optional

from homeassistant.core import HomeAssistant, callback

from .paths import config_path_str

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

# ── Pinned recognition-time face snapshots (#140 Phase 2) ───────────────────────
# When a face is recognized confidently, JARVIS grabs the camera frame AT THAT
# MOMENT and pins it, so the Faces panel shows the person as they were when seen
# — not a live view that may now be empty. One stable file per normalized name
# (latest recognition overwrites), served from /config/www → /local. Best-effort
# throughout: a capture failure never affects recognition. Cache:
#   {norm_name: {"url": "/local/...", "path": str, "ts": epoch, "camera_entity": str}}
_FACE_SNAPSHOTS: dict[str, dict] = {}
FACE_SNAPSHOT_URL_BASE = "/local/jarvis/faces"
_FACE_SNAP_THROTTLE = 300.0   # re-pin a given person at most this often (secs)
_MAX_FACE_SNAPSHOTS = 60      # prune beyond this many files

# ── Best-effort LLM resident recognition (#140 Phase 3) ─────────────────────────
# For households with NO dedicated face backend (Frigate/DoubleTake/CompreFace) —
# only JARVIS's local vision model — an opt-in path asks that model whether the
# person on camera matches an enrolled resident reference photo. It is a GUESS:
# LLM face-matching is unreliable for identity, so it is kept in a SEPARATE cache
# that `resident_present()` (the intrusion stand-down) NEVER reads — a mis-guess
# must not be able to disable an intrusion alert. It only feeds the Faces panel,
# clearly flagged low-confidence. Reference photos live privately under
# <config>/jarvis/faces_ref/<norm>.jpg (not served).
#   _LLM_GUESS_CACHE: {camera_entity: {"name": str, "confidence": float, "ts": datetime}}
_LLM_GUESS_CACHE: dict[str, dict] = {}
FACE_REF_DIR = config_path_str("jarvis", "faces_ref")


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
    now = datetime.now(timezone.utc).replace(tzinfo=None)
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
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    prev = _RECOGNITION_CACHE.get(entity_id, {})
    _RECOGNITION_CACHE[entity_id] = {
        "name":       name,
        "confidence": confidence,
        "ts":         now,
        "unknown_count": 0 if name.lower() != "unknown" else prev.get("unknown_count", 0) + 1,
    }


def _face_norm(name: str) -> str:
    """Filesystem-safe key for a recognized name, used both as the snapshot
    cache key and as the on-disk filename stem. Starts from identity.normalize
    for consistency with the roster, then hard-restricts to ``[a-z0-9_-]`` and
    caps length — the name arrives from an external backend over MQTT, so this
    must never yield a path separator, ``..``, or anything that could escape the
    snapshot directory. Returns ``""`` for a name with no usable characters."""
    try:
        from .identity import normalize
        base = normalize(name)
    except Exception:
        base = "_".join((name or "").strip().lower().split())
    import re
    safe = re.sub(r"[^a-z0-9_-]", "", (base or "").lower())
    return safe[:80]


def face_snapshot_url(name: str) -> Optional[str]:
    """The pinned recognition-time snapshot URL for ``name``, or None."""
    rec = _FACE_SNAPSHOTS.get(_face_norm(name))
    return rec.get("url") if rec else None


def _store_face_snapshot(path: str, data: bytes) -> None:
    """Write the pinned frame and prune old files. Runs on the executor."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    try:
        d = os.path.dirname(path)
        files = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".jpg")]
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for old in files[_MAX_FACE_SNAPSHOTS:]:
            try:
                os.remove(old)
            except Exception:
                pass
    except Exception:
        pass


def _should_capture_face(name: str) -> bool:
    """Gate + throttle: capture a known (non-unknown) face at most every
    _FACE_SNAP_THROTTLE seconds. Never raises."""
    try:
        key = _face_norm(name)
        if not key or _is_unknown(name):
            return False
        rec = _FACE_SNAPSHOTS.get(key)
        if rec and (_time.time() - rec.get("ts", 0)) < _FACE_SNAP_THROTTLE:
            return False
        return True
    except Exception:
        return False


async def capture_face_snapshot(hass, camera: str, name: str) -> Optional[dict]:
    """Pin the frame from ``camera`` as the recognition-time snapshot for
    ``name``. One stable file per normalized name (latest wins). Best-effort:
    returns the record or None, never raises. Gated/throttled by the caller via
    :func:`_should_capture_face`; re-checked here so direct callers are safe."""
    try:
        if not camera or not _should_capture_face(name):
            return None
        from .paths import config_path_str
        camera_entity = camera if str(camera).startswith("camera.") else f"camera.{camera}"
        from homeassistant.components.camera import async_get_image as _get_image
        image = await _get_image(hass, camera_entity, timeout=10)
        content = getattr(image, "content", None)
        if not content:
            return None
        try:
            from .camera import _downscale_jpeg
            content = _downscale_jpeg(content, 480)
        except Exception:
            pass
        key = _face_norm(name)
        snap_dir = config_path_str("www", "jarvis", "faces", hass=hass)
        path = os.path.join(snap_dir, f"{key}.jpg")
        await hass.async_add_executor_job(_store_face_snapshot, path, content)
        rec = {
            "url": f"{FACE_SNAPSHOT_URL_BASE}/{key}.jpg",
            "path": path,
            "ts": _time.time(),
            "camera_entity": camera_entity,
        }
        _FACE_SNAPSHOTS[key] = rec
        _LOGGER.info("JARVIS: pinned face snapshot for %s from %s", name, camera_entity)
        return rec
    except Exception as exc:
        _LOGGER.debug("face snapshot capture failed for %s: %s", name, exc)
        return None


def _schedule_face_capture(hass, camera: str, name: str, confidence: float) -> None:
    """From a recognition handler: schedule a recognition-time snapshot capture
    for a confident, known face. Sync + best-effort so it's safe to call from the
    MQTT callbacks; the actual grab runs as a background task."""
    try:
        if confidence < CONFIDENCE_THRESHOLD or not _should_capture_face(name):
            return
        hass.async_create_task(capture_face_snapshot(hass, camera, name))
    except Exception:
        pass


# ── Resident reference photos for best-effort LLM recognition (#140 Phase 3) ────
def set_face_reference(name: str, jpeg: bytes) -> bool:
    """Store an enrollment reference photo for ``name`` at
    ``<config>/jarvis/faces_ref/<norm>.jpg``. Returns True on success. Does file
    I/O (call on the executor). Never raises to the caller."""
    try:
        key = _face_norm(name)
        if not key or not jpeg:
            return False
        os.makedirs(FACE_REF_DIR, exist_ok=True)
        tmp = os.path.join(FACE_REF_DIR, f"{key}.jpg.tmp")
        path = os.path.join(FACE_REF_DIR, f"{key}.jpg")
        with open(tmp, "wb") as f:
            f.write(jpeg)
        os.replace(tmp, path)
        return True
    except Exception as exc:
        _LOGGER.debug("set_face_reference failed for %s: %s", name, exc)
        return False


def face_reference_path(name: str) -> Optional[str]:
    """Path to ``name``'s reference photo if enrolled, else None."""
    try:
        key = _face_norm(name)
        if not key:
            return None
        path = os.path.join(FACE_REF_DIR, f"{key}.jpg")
        return path if os.path.exists(path) else None
    except Exception:
        return None


def has_face_reference(name: str) -> bool:
    return face_reference_path(name) is not None


def remove_face_reference(name: str) -> bool:
    """Delete ``name``'s reference photo. Returns True if one was removed."""
    try:
        path = face_reference_path(name)
        if not path:
            return False
        os.remove(path)
        return True
    except Exception:
        return False


def list_face_references() -> list[str]:
    """Normalized names that currently have a reference photo enrolled."""
    try:
        if not os.path.isdir(FACE_REF_DIR):
            return []
        return sorted(f[:-4] for f in os.listdir(FACE_REF_DIR) if f.endswith(".jpg"))
    except Exception:
        return []


def remember_llm_guess(camera_name: str, name: str, confidence: float) -> None:
    """Record a BEST-EFFORT LLM resident guess (#140 Phase 3) in a cache kept
    strictly separate from the trusted recognition cache. This is deliberately
    NOT stored via remember_recognition and NEVER consulted by resident_present,
    so an unreliable guess can never stand intrusion monitoring down. Feeds only
    the Faces panel, flagged low-confidence."""
    try:
        entity_id = _camera_entity_from_name(camera_name)
        _LLM_GUESS_CACHE[entity_id] = {
            "name": name,
            "confidence": float(confidence or 0.0),
            "ts": datetime.now(timezone.utc).replace(tzinfo=None),
        }
    except Exception:
        pass


def last_seen_at(hass: HomeAssistant, camera_entity: str) -> Optional[dict]:
    """Return most recent recognition on that camera, or None if stale."""
    rec = _RECOGNITION_CACHE.get(camera_entity)
    if not rec:
        return None
    age = datetime.now(timezone.utc).replace(tzinfo=None) - rec["ts"]
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
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - CACHE_MAX_AGE
    for entity_id, rec in _RECOGNITION_CACHE.items():
        if rec["ts"] >= cutoff and rec["confidence"] >= CONFIDENCE_THRESHOLD:
            out[entity_id] = rec["name"]
    return out


def _is_unknown(name: str) -> bool:
    return (name or "").strip().lower() in ("unknown", "unknown person", "unknown_face")


def recent_faces(hass: HomeAssistant, limit: int = 20) -> list[dict]:
    """The most recent face recognition per camera, for the Household Faces panel
    (issue #140). Merges the MQTT-driven recognition cache with Frigate's
    last_recognized_face sensors, de-dupes to one row per (name, camera), marks
    each row known/unknown and whether the name is a flagged household resident,
    and returns newest first. Never raises."""
    try:
        from . import face_roster
    except Exception:
        face_roster = None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows: dict[tuple, dict] = {}

    def _add(name, cam_entity, conf, age_seconds, source, low_conf=False):
        name = (name or "").strip()
        if not name:
            return
        key = (name.lower(), cam_entity)
        prior = rows.get(key)
        if prior is not None:
            prior_low = prior.get("is_low_confidence", False)
            # A trusted recognition always outranks a best-effort LLM guess for
            # the same person/camera, regardless of which is newer.
            if low_conf and not prior_low:
                return
            # Same tier → keep the fresher sighting.
            if bool(low_conf) == bool(prior_low) and prior["age_seconds"] <= age_seconds:
                return
        unknown = _is_unknown(name)
        snap = None if unknown else _FACE_SNAPSHOTS.get(_face_norm(name))
        rows[key] = {
            "name": name,
            "camera_entity": cam_entity,
            "camera": (cam_entity or "").split(".", 1)[-1],
            "confidence": round(float(conf or 0.0), 1),
            "age_seconds": int(age_seconds),
            "is_unknown": unknown,
            "is_resident": bool(
                face_roster and not unknown and face_roster.is_resident(name)),
            # Pinned recognition-time snapshot (Phase 2) — the frame from when
            # JARVIS last saw this person; None falls back to the live camera view.
            "snapshot_url": snap.get("url") if snap else None,
            # Phase 3: a best-effort LLM guess, not a backend recognition. The
            # panel badges these distinctly and they never drive intrusion.
            "is_low_confidence": bool(low_conf),
            "source": source,
        }

    try:
        for cam, rec in _RECOGNITION_CACHE.items():
            ts = rec.get("ts")
            age = int((now - ts).total_seconds()) if ts else 10 ** 9
            if ts and (now - ts) > CACHE_MAX_AGE:
                continue
            _add(rec.get("name"), cam, rec.get("confidence", 0.0), age, "recent_cache")
    except Exception:
        pass
    try:
        for f in read_frigate_face_sensors(hass):
            # Sensors report the last recognized face with no timestamp; treat as
            # the oldest tier so a live cache hit for the same person wins.
            _add(f.get("name"), f.get("camera_entity"), f.get("confidence", 0.0),
                 10 ** 9, "frigate_sensor")
    except Exception:
        pass
    try:
        # Best-effort LLM guesses (Phase 3), flagged low-confidence so the panel
        # can mark them clearly. A trusted recognition for the same person/camera
        # already in `rows` wins (it was added first and _add keeps the fresher).
        for cam, rec in _LLM_GUESS_CACHE.items():
            ts = rec.get("ts")
            age = int((now - ts).total_seconds()) if ts else 10 ** 9
            if ts and (now - ts) > CACHE_MAX_AGE:
                continue
            _add(rec.get("name"), cam, rec.get("confidence", 0.0), age,
                 "llm_guess", low_conf=True)
    except Exception:
        pass

    out = sorted(rows.values(), key=lambda r: r["age_seconds"])
    return out[: max(1, int(limit or 20))]


def resident_present(hass: HomeAssistant, within_secs: int = 180) -> Optional[str]:
    """Name of a flagged household resident recognized on any camera within the
    last ``within_secs`` (confidently), else None. Used to stand intrusion
    monitoring down when a known resident is the one JARVIS is seeing (#140).
    Never raises.

    SAFETY (#140 Phase 3): this reads ONLY the trusted ``_RECOGNITION_CACHE``
    (backend recognitions). It deliberately never consults ``_LLM_GUESS_CACHE`` —
    a best-effort LLM guess must not be able to disable an intrusion alert."""
    try:
        from . import face_roster
    except Exception:
        return None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    best = None
    try:
        for rec in _RECOGNITION_CACHE.values():
            name = rec.get("name", "")
            ts = rec.get("ts")
            if not name or _is_unknown(name) or not ts:
                continue
            if (now - ts).total_seconds() > within_secs:
                continue
            if rec.get("confidence", 0.0) < CONFIDENCE_THRESHOLD:
                continue
            if face_roster.is_resident(name):
                best = name
                break
    except Exception:
        return None
    return best


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
        age = int((datetime.now(timezone.utc).replace(tzinfo=None) - rec["ts"]).total_seconds())
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
        _schedule_face_capture(hass, camera, name, confidence)

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

                # Bridge Frigate's MQTT events onto the HA event bus so JARVIS's
                # camera auto-analysis (_auto_frigate) and event-snapshot cache
                # (_handle_frigate_event) work without a user automation — they
                # listen for 'frigate_event', which the stock Frigate integration
                # never emits. The bus consumers apply their own label filter,
                # per-camera throttle, and notability gate. (v7.92.0)
                if event_type == "new":
                    try:
                        hass.bus.async_fire("frigate_event", payload)
                    except Exception:
                        pass

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
                        _schedule_face_capture(hass, camera, sub_name, sub_conf)
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
                        _schedule_face_capture(hass, camera, name, conf)
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
