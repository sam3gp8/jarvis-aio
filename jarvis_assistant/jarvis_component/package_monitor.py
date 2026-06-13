"""
JARVIS package & mail detection.

Watches porch / doorbell cameras for delivered packages and mail using a focused
vision classification, and tracks per-camera state so:

  • a package is announced ONCE when it arrives — not on every check while it sits
  • mail arrival is announced once
  • a package vanishing while nobody is home is flagged (possible porch theft)

Two things drive it: a low-frequency periodic check (so deliveries that don't
ring the bell are still caught) and the doorbell-press analysis (which already
describes the doorway — we reuse its text, no extra vision call). Both funnel
through the same state machine in `evaluate`.

Vision and capture plumbing is reused from camera.py via lazy import to avoid a
circular dependency; the module itself is otherwise dependency-light.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# Per-camera state: entity_id -> {"package": bool, "mail": bool, "count": int,
#                                 "since": datetime, "desc": str}
_STATE: dict[str, dict] = {}

_PKG_KEYWORDS = re.compile(
    r"\b(package|parcel|box|delivery|delivered|amazon|ups|fedex|usps|dhl|"
    r"carton|crate|cardboard)\b", re.I,
)
_MAIL_KEYWORDS = re.compile(
    r"\b(mail|letter|letters|envelope|envelopes|mailman|mail\s*carrier|"
    r"postal|postman|post)\b", re.I,
)

_PKG_PROMPT = (
    "You are inspecting a still frame from a doorway / front-porch security "
    "camera. Report ONLY whether a delivered PACKAGE or MAIL is visible right "
    "now.\n"
    "• package = a parcel, box, or delivery item sitting on the ground, step, "
    "porch, or by the door.\n"
    "• mail = letters or envelopes left at the door, or a mail carrier actively "
    "delivering.\n"
    "Ignore the street, passing vehicles, and people who are NOT leaving or "
    "carrying a delivery. If unsure, say false.\n"
    "Respond with ONLY a compact JSON object and no other text:\n"
    '{"package": true|false, "mail": true|false, "count": <number of packages>, '
    '"description": "<=12 words"}'
)


# ── Detection ────────────────────────────────────────────────────────────────

def _parse_detection(text: str) -> Optional[dict]:
    """Parse the model's JSON reply; tolerant of code fences / stray prose."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    pkg = bool(d.get("package"))
    try:
        count = int(d.get("count") or (1 if pkg else 0))
    except Exception:
        count = 1 if pkg else 0
    return {
        "package": pkg,
        "mail": bool(d.get("mail")),
        "count": max(count, 1) if pkg else 0,
        "description": str(d.get("description") or "")[:120],
    }


def detection_from_text(text: str) -> dict:
    """Keyword-based detection from an existing free-text analysis (e.g. the
    doorbell-press description). A cheap reuse — no extra vision call."""
    has_pkg = bool(_PKG_KEYWORDS.search(text or ""))
    return {
        "package": has_pkg,
        "mail": bool(_MAIL_KEYWORDS.search(text or "")),
        "count": 1 if has_pkg else 0,
        "description": "",
    }


async def detect_on_camera(hass, groq_client, entity_id: str) -> Optional[dict]:
    """Focused package/mail vision classification on a fresh frame."""
    from . import camera as cam  # lazy to avoid circular import

    img = await cam._get_best_image(hass, entity_id)
    if not img:
        return None
    img = cam._downscale_jpeg(img)
    provider = cam._cfg_opt(hass, "vision_provider", "groq") or "groq"
    model = cam._cfg_opt(hass, "vision_model", cam.VISION_MODEL) or cam.VISION_MODEL
    client = cam._make_client(hass, provider, model, groq_client)
    b64 = base64.b64encode(img).decode()
    try:
        result = await hass.async_add_executor_job(
            lambda: client.chat(
                messages=[
                    {"role": "system", "content": _PKG_PROMPT},
                    {"role": "user", "content": [
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": "Classify per the instructions. JSON only."},
                    ]},
                ],
                max_tokens=120,
                model_override=model or None,
            )
        )
        text = (result.get("text") or "").strip()
    except Exception as exc:
        _LOGGER.debug("JARVIS package vision error on %s: %s", entity_id, exc)
        return None
    return _parse_detection(text) or detection_from_text(text)


# ── Gating helpers ───────────────────────────────────────────────────────────

def _runtime(hass, key, default):
    try:
        from .const import DOMAIN
        for data in (hass.data.get(DOMAIN) or {}).values():
            if isinstance(data, dict) and isinstance(data.get("runtime_config"), dict):
                rc = data["runtime_config"]
                if key in rc:
                    return rc[key]
                break
    except Exception:
        pass
    return default


def _announcements_on(hass) -> bool:
    v = _runtime(hass, "announcements_enabled", True)
    return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")


def _in_quiet_hours(hass) -> bool:
    try:
        from . import sleep_detection
        return sleep_detection._in_quiet_hours(
            str(_runtime(hass, "observer_quiet_start", "22:00")),
            str(_runtime(hass, "observer_quiet_end", "07:00")),
        )
    except Exception:
        return False


def _anyone_home(hass) -> bool:
    """Best-effort presence. Defaults to True (assume home) when undeterminable —
    so a 'package removed while away' alert never false-fires on unknown state."""
    try:
        persons = hass.states.async_all("person")
        if persons:
            if any(st.state == "home" for st in persons):
                return True
            # All persons known and none home → away
            if all(st.state not in ("unknown", "unavailable") for st in persons):
                return False
        z = hass.states.get("zone.home")
        if z and str(z.state).isdigit():
            return int(z.state) > 0
    except Exception:
        pass
    return True


def watched_cameras(hass, configured=None) -> list[str]:
    """Resolve which cameras to inspect for deliveries."""
    if configured:
        if isinstance(configured, str):
            return [configured] if hass.states.get(configured) else []
        return [c for c in configured if hass.states.get(c)]
    out = []
    for st in hass.states.async_all("camera"):
        e = st.entity_id
        if any(k in e for k in ("doorbell", "front_door", "porch", "front")):
            out.append(e)
    return out


# ── State machine + announcements ────────────────────────────────────────────

def _log(hass, entity_id: str, kind: str, det: dict, source: str) -> None:
    try:
        from .websocket import jarvis_log
        jarvis_log("CAMERA", f"{entity_id} package-monitor [{source}]: {kind} "
                             f"(pkg={det.get('package')} mail={det.get('mail')} "
                             f"n={det.get('count')})")
    except Exception:
        pass
    try:
        from . import observer
        note = {
            "delivered": "A package was delivered",
            "mail": "Mail arrived",
            "removed": "A package was removed",
        }.get(kind, kind)
        observer.record_camera_event(entity_id, note, "delivery",
                                     notable=(kind in ("delivered", "removed", "mail")))
    except Exception:
        pass


async def evaluate(hass, groq_client, honorific, tts_entity, speakers,
                   entity_id: str, det: dict, source: str = "periodic") -> bool:
    """Apply a detection result to per-camera state and announce transitions."""
    from .tts_helper import async_announce

    prev = _STATE.get(entity_id, {"package": False, "mail": False, "count": 0})
    quiet = _in_quiet_hours(hass)
    can_speak = _announcements_on(hass) and not quiet
    loc = "the front door"
    spoke = False

    # Package arrival
    if det.get("package") and not prev.get("package"):
        n = det.get("count", 1)
        msg = (f"{honorific}, {n} packages have been delivered to {loc}."
               if n and n > 1 else
               f"{honorific}, a package has been delivered to {loc}.")
        _log(hass, entity_id, "delivered", det, source)
        if can_speak:
            await async_announce(hass, msg, tts_entity, speakers, context="package")
            spoke = True
    # Package removed
    elif prev.get("package") and not det.get("package"):
        away = not _anyone_home(hass)
        _log(hass, entity_id, "removed", det, source)
        if away and can_speak:
            await async_announce(
                hass,
                f"{honorific}, a package was just removed from {loc} while no one is home.",
                tts_entity, speakers, context="package",
            )
            spoke = True

    # Mail arrival
    if det.get("mail") and not prev.get("mail"):
        _log(hass, entity_id, "mail", det, source)
        if can_speak:
            await async_announce(
                hass, f"{honorific}, mail has arrived at {loc}.",
                tts_entity, speakers, context="package",
            )
            spoke = True

    _STATE[entity_id] = {
        "package": bool(det.get("package")),
        "mail": bool(det.get("mail")),
        "count": int(det.get("count", 0) or 0),
        "since": datetime.utcnow(),
        "desc": det.get("description", ""),
    }
    return spoke


async def periodic_check(hass, groq_client, honorific, tts_entity, speakers,
                         configured_camera=None) -> dict:
    """Inspect each watched camera once. Skipped during quiet hours (nothing is
    announced then anyway, and it saves vision calls overnight)."""
    if _in_quiet_hours(hass):
        return {"skipped": "quiet_hours"}
    cams = watched_cameras(hass, configured_camera)
    checked = 0
    for entity_id in cams:
        det = await detect_on_camera(hass, groq_client, entity_id)
        if det is None:
            continue
        await evaluate(hass, groq_client, honorific, tts_entity, speakers,
                       entity_id, det, source="periodic")
        checked += 1
    return {"checked": checked, "cameras": cams}


async def note_from_doorbell(hass, groq_client, honorific, tts_entity, speakers,
                             entity_id: str, analysis_text: str) -> None:
    """Hook for the doorbell-press flow: derive package/mail from the press
    analysis text (no extra vision call) and run it through the state machine."""
    det = detection_from_text(analysis_text)
    if not (det["package"] or det["mail"]):
        # Nothing delivery-like seen; still record absence so a later pickup of a
        # previously-seen package can be detected — but only if we were tracking
        # this camera already.
        if entity_id not in _STATE:
            return
    await evaluate(hass, groq_client, honorific, tts_entity, speakers,
                   entity_id, det, source="doorbell")


def status() -> dict:
    """Current tracked package/mail state, for panel/diagnostics."""
    return {
        eid: {
            "package": s.get("package"),
            "mail": s.get("mail"),
            "count": s.get("count"),
            "desc": s.get("desc", ""),
        }
        for eid, s in _STATE.items()
    }
