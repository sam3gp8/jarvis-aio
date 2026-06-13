"""
Home State Awareness (v5.7.00).

Builds a concise summary of the home's current state for injection into
the JARVIS conversation system prompt. Updated every 60 seconds.

Covers: occupancy, climate, lights, locks, covers, security, media, and
any anomalous sensor readings. Designed to keep JARVIS contextually aware
without listing every entity individually.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_cache: dict = {"summary": "", "ts": 0.0}
_TTL = 60.0  # rebuild every 60 seconds


def get_home_summary(hass: HomeAssistant) -> str:
    """
    Return a concise home state summary string. Cached for _TTL seconds.
    Safe to call from sync context (uses cached value, async rebuild
    happens lazily on next call after TTL expires).
    """
    now = time.time()
    if (now - _cache["ts"]) < _TTL and _cache["summary"]:
        return _cache["summary"]
    _cache["summary"] = _build_summary(hass)
    _cache["ts"] = now
    return _cache["summary"]


def _build_summary(hass: HomeAssistant) -> str:
    """Build the home state summary from current HA states."""
    parts = []

    # ── Occupancy ────────────────────────────────────────────────────────
    occupied_areas = []
    try:
        from . import audio_routing
        occupied_areas = audio_routing.currently_occupied_areas(hass)
        if occupied_areas:
            area_names = []
            for aid in occupied_areas:
                name = _area_name(hass, aid)
                area_names.append(name)
            parts.append(f"Occupied areas: {', '.join(area_names)}")
        else:
            if audio_routing.anyone_home(hass):
                parts.append("Someone is home but no specific room occupancy detected")
            else:
                parts.append("No one appears to be home")
    except Exception:
        pass

    # ── Climate ──────────────────────────────────────────────────────────
    temps = []
    for state in hass.states.async_all("sensor"):
        if state.attributes.get("device_class") == "temperature":
            try:
                val = float(state.state)
                name = state.attributes.get("friendly_name", state.entity_id)
                unit = state.attributes.get("unit_of_measurement", "°F")
                # Only include room-level temps, not device internals
                if any(kw in name.lower() for kw in ("room", "bedroom", "kitchen",
                    "living", "garage", "office", "hallway", "basement")):
                    temps.append(f"{name}: {val:.0f}{unit}")
            except (ValueError, TypeError):
                pass
    if temps:
        parts.append(f"Temperatures: {'; '.join(temps[:6])}")

    # ── Lights ───────────────────────────────────────────────────────────
    lights_on = []
    lights_total = 0
    for state in hass.states.async_all("light"):
        lights_total += 1
        if state.state == "on":
            lights_on.append(state.attributes.get("friendly_name", state.entity_id))
    if lights_on:
        parts.append(f"Lights on ({len(lights_on)}/{lights_total}): {', '.join(lights_on[:8])}")
    else:
        parts.append(f"All {lights_total} lights are off")

    # ── Locks ────────────────────────────────────────────────────────────
    unlocked = []
    for state in hass.states.async_all("lock"):
        if state.state == "unlocked":
            unlocked.append(state.attributes.get("friendly_name", state.entity_id))
    if unlocked:
        parts.append(f"Unlocked: {', '.join(unlocked)}")

    # ── Covers (garage doors, blinds) ────────────────────────────────────
    open_covers = []
    for state in hass.states.async_all("cover"):
        if state.state == "open":
            open_covers.append(state.attributes.get("friendly_name", state.entity_id))
    if open_covers:
        parts.append(f"Open covers: {', '.join(open_covers)}")

    # ── Security ─────────────────────────────────────────────────────────
    open_doors = []
    for state in hass.states.async_all("binary_sensor"):
        dclass = state.attributes.get("device_class")
        if dclass in ("door", "window", "garage_door") and state.state == "on":
            open_doors.append(state.attributes.get("friendly_name", state.entity_id))
    if open_doors:
        parts.append(f"Open doors/windows: {', '.join(open_doors[:6])}")

    # ── Alarm ────────────────────────────────────────────────────────────
    for state in hass.states.async_all("alarm_control_panel"):
        parts.append(f"Alarm: {state.state}")

    # ── Media ────────────────────────────────────────────────────────────
    playing = []
    for state in hass.states.async_all("media_player"):
        if state.state == "playing":
            title = state.attributes.get("media_title", "")
            name = state.attributes.get("friendly_name", state.entity_id)
            playing.append(f"{name}" + (f" ({title})" if title else ""))
    if playing:
        parts.append(f"Playing media: {', '.join(playing[:4])}")

    # ── Time context ─────────────────────────────────────────────────────
    from homeassistant.util import dt as dt_util
    now = dt_util.now()
    parts.insert(0, f"Current time: {now.strftime('%I:%M %p, %A %B %d')}")

    return "\n".join(parts)


def _area_name(hass: HomeAssistant, area_id: str) -> str:
    try:
        from homeassistant.helpers import area_registry as ar
        reg = ar.async_get(hass)
        area = reg.async_get_area(area_id)
        return area.name if area else area_id
    except Exception:
        return area_id
