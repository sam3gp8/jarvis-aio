"""JARVIS mode entry actions (v7.15.0).

A mode can set a *mood* the moment it activates. Kept separate from modes.py
(which is deliberately hass-free persisted state) so the actuation — which needs
Home Assistant — lives here and is invoked from the set-mode entry points.

Currently: Movie mode dims the lights in its bound room to a user-set level.
Never raises; a mood action must never break a mode switch.
"""
from __future__ import annotations

import logging
from typing import Optional

_LOGGER = logging.getLogger(__name__)


def _movie_area(hass, jarvis_config, audio_routing) -> Optional[str]:
    area = jarvis_config.get("movie_area", "") or ""
    if area:
        return str(area)
    mp = jarvis_config.get("movie_media_player", "") or ""
    if mp:
        return audio_routing.entity_area(hass, mp)
    return None


async def apply_mode_entry(hass, mode: str) -> None:
    """Apply a mode's on-activation mood. Movie → dim the bound room's lights."""
    try:
        if mode != "movie":
            return
        from . import jarvis_config, audio_routing
        area = _movie_area(hass, jarvis_config, audio_routing)
        if not area:
            return
        try:
            pct = int(jarvis_config.get("movie_dim_pct", 15))
        except Exception:
            pct = 15
        pct = max(0, min(100, pct))
        lights = [
            s.entity_id for s in hass.states.async_all("light")
            if audio_routing.entity_area(hass, s.entity_id) == area
        ]
        if not lights:
            return
        if pct <= 0:
            await hass.services.async_call(
                "light", "turn_off", {"entity_id": lights}, blocking=False)
        else:
            await hass.services.async_call(
                "light", "turn_on",
                {"entity_id": lights, "brightness_pct": pct}, blocking=False)
        _LOGGER.info("Movie mood: dimmed %d light(s) in %s to %d%%",
                     len(lights), area, pct)
    except Exception as exc:
        _LOGGER.debug("apply_mode_entry(%s) failed: %s", mode, exc)
