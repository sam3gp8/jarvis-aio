"""
JARVIS — Intrusion snapshots & false-alarm call-off (v6.68.0).

Two additions to the existing SafetyManager intrusion flow:

  1. Snapshot: when an intrusion is confirmed on camera, grab a still from that
     camera and make it available with the alert — so the notification can show
     WHO/what triggered it, and the panel can display it.

  2. Call-off: let the user declare a false alarm. dismiss_intrusion() clears the
     active investigation, suppresses further escalation for a cooldown, and
     records the false alarm so repeated benign triggers can be learned from.

Snapshots are written under /config/www/jarvis/intrusion (served at
/local/jarvis/intrusion/...) so HA can render them in notifications and the
panel. Everything here is defensive and never raises to the caller.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# Servable snapshot dir: /config/www/... is exposed at /local/...
SNAPSHOT_DIR = "/config/www/jarvis/intrusion"
SNAPSHOT_URL_BASE = "/local/jarvis/intrusion"
_MAX_SNAPSHOTS = 40           # keep the last N, prune older

# Call-off state (module-level; the investigation itself lives in SafetyManager)
_called_off_until = 0.0       # suppress escalation until this ts
_CALLOFF_COOLDOWN = 600.0     # 10 min quiet after a false-alarm call-off
_last_snapshot: dict = {}     # {path, url, camera, ts} of the most recent capture
_false_alarms: list = []      # recent {ts, camera, area} for learning


async def capture_snapshot(hass, camera_entity: str,
                           tag: str = "intrusion") -> Optional[dict]:
    """Grab a still from camera_entity and write it to the servable dir. Returns
    {path, url, camera, ts} or None. Never raises."""
    if not camera_entity:
        return None
    try:
        from homeassistant.components.camera import async_get_image as _get_image
        image = await _get_image(hass, camera_entity, timeout=10)
        content = getattr(image, "content", None)
        if not content:
            return None
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        ts = int(time.time())
        slug = camera_entity.split(".", 1)[-1]
        fname = f"{tag}_{slug}_{ts}.jpg"
        path = os.path.join(SNAPSHOT_DIR, fname)
        await hass.async_add_executor_job(_write_bytes, path, content)
        _prune_old()
        info = {
            "path": path,
            "url": f"{SNAPSHOT_URL_BASE}/{fname}",
            "camera": camera_entity,
            "ts": ts,
        }
        global _last_snapshot
        _last_snapshot = info
        _LOGGER.info("JARVIS: intrusion snapshot saved from %s → %s",
                     camera_entity, info["url"])
        return info
    except Exception as exc:
        _LOGGER.debug("intrusion snapshot failed for %s: %s", camera_entity, exc)
        return None


def _write_bytes(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


def _prune_old() -> None:
    try:
        files = [
            os.path.join(SNAPSHOT_DIR, f)
            for f in os.listdir(SNAPSHOT_DIR)
            if f.endswith(".jpg")
        ]
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for old in files[_MAX_SNAPSHOTS:]:
            try:
                os.remove(old)
            except Exception:
                pass
    except Exception:
        pass


def last_snapshot() -> Optional[dict]:
    """The most recent intrusion snapshot info, or None."""
    return _last_snapshot or None


# ── false-alarm call-off ─────────────────────────────────────────────────────

def dismiss_intrusion(reason: str = "") -> dict:
    """Declare the current/last intrusion a false alarm. Sets a suppression
    window so the SafetyManager stops escalating, and records it. The
    SafetyManager consults is_called_off() and clears its investigation. Never
    raises."""
    global _called_off_until
    _called_off_until = time.time() + _CALLOFF_COOLDOWN
    rec = {
        "ts": int(time.time()),
        "reason": str(reason or ""),
        "camera": (_last_snapshot or {}).get("camera", ""),
    }
    _false_alarms.append(rec)
    if len(_false_alarms) > 50:
        del _false_alarms[:-50]
    _LOGGER.info("JARVIS: intrusion called off by user%s — suppressing escalation "
                 "for %ds", f" ({reason})" if reason else "", int(_CALLOFF_COOLDOWN))
    return {"ok": True, "suppressed_seconds": int(_CALLOFF_COOLDOWN), "recorded": rec}


def is_called_off() -> bool:
    """Whether a user call-off is currently suppressing escalation."""
    return time.time() < _called_off_until


def clear_calloff() -> None:
    """Reset the suppression (e.g. on a genuinely new, unrelated trigger)."""
    global _called_off_until
    _called_off_until = 0.0


def false_alarm_count(within_seconds: float = 86400.0) -> int:
    """How many false alarms the user has called off recently (for learning /
    threshold tuning). Default window: 24h."""
    cutoff = time.time() - within_seconds
    return sum(1 for r in _false_alarms if r.get("ts", 0) >= cutoff)


def status() -> dict:
    """Snapshot + call-off status for the panel/agent."""
    return {
        "last_snapshot": _last_snapshot or None,
        "called_off": is_called_off(),
        "suppressed_for": max(0, int(_called_off_until - time.time())),
        "false_alarms_24h": false_alarm_count(),
    }
