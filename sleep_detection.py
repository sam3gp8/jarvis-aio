"""
JARVIS — Sleep detection (v5.7.00, area-driven).

Determines whether Sam is asleep or napping. Rules (simple and explainable):

  SLEEPING =
    (occupancy in any bedroom-flagged area) AND (in quiet hours)
    OR
    (manual nap service still active)

User configures bedroom areas via a per-area toggle in the JARVIS options flow.
No ML, no opaque classifier.

Integrates with:
  - bedroom_areas: list of HA area_ids user flagged as bedrooms
  - observer_quiet_start / _end: the nightly quiet hours window
  - jarvis.nap service: manual override
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Iterable, Optional

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .audio_routing import is_any_bedroom_occupied

_LOGGER = logging.getLogger(__name__)


# Module-level manual nap override
_NAP_UNTIL: Optional[datetime] = None


def set_nap(duration_minutes: int = 30) -> None:
    """Called by jarvis.nap service — sets manual mute for N minutes."""
    global _NAP_UNTIL
    _NAP_UNTIL = dt_util.utcnow() + timedelta(minutes=duration_minutes)
    _LOGGER.info("JARVIS: manual nap for %d min until %s", duration_minutes, _NAP_UNTIL)


def clear_nap() -> None:
    """Explicit wake — cancels manual nap override."""
    global _NAP_UNTIL
    _NAP_UNTIL = None
    _LOGGER.info("JARVIS: manual nap cleared")


def _in_quiet_hours(quiet_start: str, quiet_end: str) -> bool:
    """Check if current local time is within the configured quiet window.
    Handles windows that cross midnight (e.g. 22:00 → 07:00)."""
    try:
        start = time.fromisoformat(quiet_start)
        end   = time.fromisoformat(quiet_end)
    except (ValueError, TypeError):
        _LOGGER.warning("invalid quiet hours '%s' -> '%s'", quiet_start, quiet_end)
        return False
    now_local = dt_util.now().time()
    if start <= end:
        return start <= now_local < end
    return now_local >= start or now_local < end


def is_sleeping(
    hass: HomeAssistant,
    *,
    bedroom_area_ids: Iterable[str] = (),
    quiet_start: str = "22:00",
    quiet_end: str = "07:00",
) -> tuple[bool, str]:
    """
    Return (sleeping, reason_string).

    Priority:
      1. Manual nap override (highest)
      2. Bedroom occupancy during quiet hours
      3. Otherwise not sleeping
    """
    global _NAP_UNTIL

    # Manual nap check
    if _NAP_UNTIL is not None:
        if dt_util.utcnow() < _NAP_UNTIL:
            minutes_left = (_NAP_UNTIL - dt_util.utcnow()).total_seconds() / 60
            return True, f"manual nap ({minutes_left:.0f}min remaining)"
        _NAP_UNTIL = None

    # Bedroom + quiet hours
    if bedroom_area_ids and _in_quiet_hours(quiet_start, quiet_end):
        occupied, area_id = is_any_bedroom_occupied(hass, bedroom_area_ids)
        if occupied:
            return True, f"bedroom ({area_id}) occupied during quiet hours"

    return False, "awake"


def should_suppress(
    hass: HomeAssistant,
    *,
    urgency: str,
    bedroom_area_ids: Iterable[str] = (),
    quiet_start: str = "22:00",
    quiet_end: str = "07:00",
) -> tuple[bool, str]:
    """
    Should this announcement be suppressed due to sleep state?

    Critical NEVER suppresses (safety override).
    High:   audio suppressed, caller may still send push notification.
    Medium/Low: always suppressed when sleeping.
    """
    if urgency == "critical":
        return False, "critical urgency overrides sleep"

    sleeping, reason = is_sleeping(
        hass,
        bedroom_area_ids=bedroom_area_ids,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
    )
    if sleeping:
        return True, reason
    return False, "awake"
