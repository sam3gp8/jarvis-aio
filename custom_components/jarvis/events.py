"""Integration-level event publishing (kernel Phase 1, shadow mode).

A thin, HA-aware bridge between publishers (observer, camera, voice) and the pure
``kernel`` event bus held in ``hass.data``. Publishers call :func:`publish`
best-effort; if the bus isn't present (setup incomplete, or an older entry) it is
a silent no-op. Nothing here ever raises into a caller's authoritative path.
"""
from __future__ import annotations

import logging

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def get_event_bus(hass):
    """Return the live JarvisEventBus for this hass, or None if unavailable."""
    try:
        data = hass.data.get(DOMAIN) or {}
        for entry_data in data.values():
            if isinstance(entry_data, dict) and entry_data.get("event_bus") is not None:
                return entry_data["event_bus"]
    except Exception:
        pass
    return None


def publish(hass, event) -> None:
    """Publish a JarvisEvent on the bus, best-effort (never raises)."""
    bus = get_event_bus(hass)
    if bus is None:
        return
    try:
        bus.publish(event)
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("event publish failed: %s", exc)


def publish_situation(hass, situation, *, action=None) -> None:
    """Publish a situation lifecycle transition as a canonical JarvisEvent
    (MCU Phase D/D4). Best-effort — never raises into the situation mirror.

    ``situation`` is a ``kernel.situation.Situation`` (duck-typed: kind / state /
    subject / location / id / correlation_id). ``action`` is the verdict that
    drove the transition (e.g. "confirmed", "critical", "delivered")."""
    try:
        from .kernel.event import from_situation
        ev = from_situation(
            getattr(situation, "kind", None),
            state=getattr(situation, "state", None),
            action=action,
            subject=getattr(situation, "subject", None),
            location=getattr(situation, "location", None),
            situation_id=getattr(situation, "id", None),
            correlation_id=getattr(situation, "correlation_id", None),
        )
        publish(hass, ev)
    except Exception as exc:  # pragma: no cover - defensive
        _LOGGER.debug("situation event publish failed: %s", exc)
