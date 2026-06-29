"""
JARVIS doorbell training log.

Accumulates analysed doorbell events into a labelled JSON-Lines dataset that
JARVIS can later mine for visitor patterns (who comes to the door and when),
seed face recognition, and feed the cognitive core. Two sources populate it:

  • Going forward — every doorbell press analysed by camera._analyze_doorbell_press
    appends a record here automatically.
  • Backlog — `scan_backlog` walks the Nest doorbell's recorded event history and
    analyses each clip into the same log (best-effort; depends on the Nest
    integration exposing an event-media manager).

This module is intentionally dependency-light: the log itself is pure stdlib so
it can never destabilise the integration. The backlog scan touches Nest
integration internals and is wrapped so any shape mismatch reports cleanly
rather than raising.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Callable, Optional

_LOGGER = logging.getLogger(__name__)

LOG_DIR = "/config/jarvis"
LOG_PATH = os.path.join(LOG_DIR, "doorbell_log.jsonl")
MAX_RECORDS = 5000  # ring the file so it can't grow unbounded


# ── Write path ───────────────────────────────────────────────────────────────

def log_event(camera: str, entity_id: str, source: str, result: dict) -> None:
    """
    Append one analysed doorbell event to the training log. `source` is the
    frame origin ("live" | "event-media" | "backlog"). Safe to call from an
    executor thread. Never raises.
    """
    try:
        rec = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "camera": camera,
            "entity_id": entity_id,
            "image_source": source,
            "summary": (result or {}).get("summary", ""),
            "analysis": (result or {}).get("analysis", ""),
            "category": (result or {}).get("category", ""),
            "notable": bool((result or {}).get("notable", False)),
        }
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _trim_if_needed()
    except Exception as exc:  # logging must never break the caller
        _LOGGER.debug("doorbell_training.log_event failed: %s", exc)


def _trim_if_needed() -> None:
    try:
        if not os.path.exists(LOG_PATH):
            return
        with open(LOG_PATH, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        if len(lines) > MAX_RECORDS:
            with open(LOG_PATH, "w", encoding="utf-8") as fh:
                fh.writelines(lines[-MAX_RECORDS:])
    except Exception as exc:
        _LOGGER.debug("doorbell_training trim failed: %s", exc)


# ── Read path ────────────────────────────────────────────────────────────────

def load_events(limit: Optional[int] = None) -> list[dict]:
    """Return logged events (newest last). Never raises."""
    try:
        if not os.path.exists(LOG_PATH):
            return []
        out: list[dict] = []
        with open(LOG_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
        return out[-limit:] if limit else out
    except Exception as exc:
        _LOGGER.debug("doorbell_training.load_events failed: %s", exc)
        return []


def stats() -> dict:
    """Summary counts for the panel / diagnostics. Never raises."""
    events = load_events()
    by_cat: dict[str, int] = {}
    by_source: dict[str, int] = {}
    notable = 0
    for e in events:
        by_cat[e.get("category", "?")] = by_cat.get(e.get("category", "?"), 0) + 1
        by_source[e.get("image_source", "?")] = by_source.get(e.get("image_source", "?"), 0) + 1
        if e.get("notable"):
            notable += 1
    first = events[0]["ts"] if events else None
    last = events[-1]["ts"] if events else None
    return {
        "total": len(events),
        "notable": notable,
        "by_category": by_cat,
        "by_source": by_source,
        "first": first,
        "last": last,
    }


# ── Backlog scan (best-effort) ───────────────────────────────────────────────

def _iter_nest_doorbell_devices(hass) -> list[tuple[str, Any]]:
    """
    Yield (device_id, device) for Nest devices that expose event media.

    Mirrors HA core's own enumeration (nest/device_info.py::async_nest_devices):
        for entry in hass.config_entries.async_loaded_entries("nest"):
            entry.runtime_data.device_manager.devices
    with fallbacks for older cores where NestData lived in hass.data["nest"].
    """
    found: list[tuple[str, Any]] = []
    entries = []
    try:
        entries = list(hass.config_entries.async_loaded_entries("nest"))
    except AttributeError:
        try:  # older cores: filter async_entries by loaded state
            entries = [e for e in hass.config_entries.async_entries("nest")
                       if "LOADED" in str(getattr(e, "state", "")).upper()]
        except Exception as exc:
            _LOGGER.debug("doorbell_training: entry enumeration failed: %s", exc)
    for entry in entries:
        dm = None
        rd = getattr(entry, "runtime_data", None)
        if rd is not None:
            dm = getattr(rd, "device_manager", None) or (
                rd.get("device_manager") if isinstance(rd, dict) else None)
        if dm is None:  # pre-runtime_data cores
            data = (hass.data.get("nest") or {}).get(getattr(entry, "entry_id", ""), None)
            if data is not None:
                dm = getattr(data, "device_manager", None) or (
                    data.get("device_manager") if isinstance(data, dict) else None)
        devices = getattr(dm, "devices", None)
        if not devices:
            continue
        items = devices.items() if hasattr(devices, "items") else []
        for dev_id, dev in items:
            if getattr(dev, "event_media_manager", None) is not None:
                found.append((dev_id, dev))
    return found


async def _events_for_device(dev) -> list[tuple[str, Any]]:
    """
    Recorded event sessions for a device, tagged by kind. Battery doorbells/cams
    record CLIP PREVIEW sessions (mp4, thumbnailed via HA's wired transcoder);
    wired models record IMAGE sessions. Both come from the same disk store the
    Media browser displays.
    """
    emm = getattr(dev, "event_media_manager", None)
    if emm is None:
        return []
    out: list[tuple[str, Any]] = []
    for kind, attr in (("clip", "async_clip_preview_sessions"),
                       ("image", "async_image_sessions")):
        fn = getattr(emm, attr, None)
        if fn is None:
            continue
        try:
            res = fn()
            if hasattr(res, "__aiter__"):
                items = [item async for item in res]
            else:
                items = list(await res or [])
            out.extend((kind, ev) for ev in items)
        except Exception as exc:
            _LOGGER.debug("doorbell_training: %s failed: %s", attr, exc)
    return out


async def _event_image_bytes(dev, kind: str, event_obj) -> Optional[bytes]:
    """Fetch a vision-usable JPEG for an event: image sessions via
    get_media_from_token; clip sessions via the transcoded thumbnail."""
    emm = getattr(dev, "event_media_manager", None)
    if emm is None:
        return None
    token = getattr(event_obj, "event_token", None)
    if not token:
        return None
    attrs = (("get_clip_thumbnail_from_token", "get_media_from_token")
             if kind == "clip" else
             ("get_media_from_token", "get_clip_thumbnail_from_token"))
    for attr in attrs:
        fn = getattr(emm, attr, None)
        if fn is None:
            continue
        try:
            media = await fn(token)
            data = getattr(media, "contents", None) if media is not None else None
            # Clip MP4 bytes are useless to a vision model — only accept media
            # that looks like an image (thumbnail path yields JPEG).
            if data and len(data) > 2000 and not data[4:12].startswith(b"ftyp"):
                return data
        except Exception as exc:
            _LOGGER.debug("doorbell_training: %s failed: %s", attr, exc)
    return None


async def scan_backlog(
    hass,
    analyze_image: Callable,
    honorific: str,
    limit: int = 40,
) -> dict:
    """
    Walk recorded Nest doorbell events and analyse each into the training log.

    `analyze_image(image_bytes, label) -> dict` is supplied by the caller (it
    wraps camera.async_analyze_camera with force_images). Returns a report dict;
    never raises. Best-effort: if the Nest integration doesn't expose an event
    media manager in a shape we recognise, `analyzed` will be 0 and `reason`
    explains it.
    """
    report = {"ok": False, "devices": 0, "found": 0, "analyzed": 0,
              "no_image": 0, "errors": [], "reason": ""}
    devices = _iter_nest_doorbell_devices(hass)
    report["devices"] = len(devices)
    if not devices:
        report["reason"] = (
            "No loaded Nest config entry exposed a device manager with event "
            "media. Discovery mirrors HA core's own enumeration "
            "(entry.runtime_data.device_manager), so if the Media browser shows "
            "events, check that the Nest entry is fully loaded."
        )
        return report

    remaining = max(1, int(limit))
    for dev_id, dev in devices:
        if remaining <= 0:
            break
        try:
            events = await _events_for_device(dev)
        except Exception as exc:
            report["errors"].append(f"events:{exc}")
            continue
        report["found"] += len(events)
        for kind, ev in events:
            if remaining <= 0:
                break
            try:
                img = await _event_image_bytes(dev, kind, ev)
                if not img:
                    report["no_image"] += 1
                    continue
                etypes = (getattr(ev, "event_types", None)
                          or [getattr(ev, "event_type", None)] or [])
                label = ", ".join(str(t).rsplit(".", 1)[-1] for t in etypes if t) \
                        or "doorbell event"
                res = await analyze_image(img, label)
                if res and res.get("success"):
                    cam = res.get("camera", dev_id)
                    log_event(cam, f"nest:{dev_id}", "backlog", res)
                    report["analyzed"] += 1
                    remaining -= 1
            except Exception as exc:
                report["errors"].append(str(exc))
                continue

    report["ok"] = report["analyzed"] > 0
    if not report["ok"] and not report["reason"]:
        if report["found"] and report["no_image"] == report["found"]:
            report["reason"] = (
                f"Found {report['found']} event(s) but none yielded an image — "
                f"clip thumbnails require ffmpeg transcoding via the Nest store; "
                f"check home-assistant.log for transcoder errors."
            )
        else:
            report["reason"] = (
                f"Found {report['found']} event record(s) but could not extract "
                f"analysable images ({report['no_image']} without media)."
            )
    report["errors"] = report["errors"][:5]
    return report
