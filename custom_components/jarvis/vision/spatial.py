"""Spatial context fusion for JARVIS.

``SpatialContextEngine`` fuses three per-area presence signals into a single
occupancy-confidence score and decides whether an announcement can drop its
preamble (speak straight to the point because the user is demonstrably present
and looking):

    sensor.{area}_frigate_person_count        > 0   → +0.60
    binary_sensor.{area}_camera_gaze_detected  on   → +0.20
    binary_sensor.{area}_mmwave_presence       on   → +0.35

Confidence is clamped to [0.0, 1.0]. When gaze AND mmWave presence are both
established, ``skip_preamble`` is True.

Reads the Home Assistant state machine but imports no HA modules, so it stays a
pure, directly-testable leaf.
"""
from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

_ON_STATES = {"on", "true", "detected", "home", "occupied"}


class SpatialContextEngine:
    """Fuse Frigate person-object, camera-gaze, and mmWave presence per area."""

    PERSON_COUNT_WEIGHT = 0.60
    GAZE_WEIGHT = 0.20
    PRESENCE_WEIGHT = 0.35

    CONFIDENCE_MIN = 0.0
    CONFIDENCE_MAX = 1.0

    def __init__(self, hass) -> None:
        self.hass = hass

    # ── Signal readers (each fully guarded) ───────────────────────────────
    def _person_count(self, area_id: str) -> int:
        eid = f"sensor.{area_id}_frigate_person_count"
        state = self.hass.states.get(eid)
        if state is None:
            return 0
        try:
            return max(0, int(float(state.state)))
        except (TypeError, ValueError):
            return 0

    def _binary_on(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        if state is None:
            return False
        return str(state.state).lower() in _ON_STATES

    # ── Fusion ────────────────────────────────────────────────────────────
    def evaluate(self, area_id: str) -> dict:
        """Return the fused spatial context for ``area_id``.

        Keys:
            confidence    (float, 0.0–1.0) – fused occupancy confidence
            skip_preamble (bool)           – gaze AND presence established
            person_count  (int)            – Frigate person objects in view
            gaze          (bool)           – camera gaze detected
            presence      (bool)           – mmWave presence detected
        """
        person_count = self._person_count(area_id)
        gaze = self._binary_on(f"binary_sensor.{area_id}_camera_gaze_detected")
        presence = self._binary_on(f"binary_sensor.{area_id}_mmwave_presence")

        confidence = 0.0
        if person_count > 0:
            confidence += self.PERSON_COUNT_WEIGHT
        if gaze:
            confidence += self.GAZE_WEIGHT
        if presence:
            confidence += self.PRESENCE_WEIGHT
        confidence = max(self.CONFIDENCE_MIN, min(self.CONFIDENCE_MAX, confidence))

        skip_preamble = bool(gaze and presence)

        _LOGGER.debug(
            "spatial[%s]: persons=%d gaze=%s presence=%s → conf=%.2f skip_preamble=%s",
            area_id, person_count, gaze, presence, confidence, skip_preamble,
        )
        return {
            "confidence": round(confidence, 2),
            "skip_preamble": skip_preamble,
            "person_count": person_count,
            "gaze": gaze,
            "presence": presence,
        }
