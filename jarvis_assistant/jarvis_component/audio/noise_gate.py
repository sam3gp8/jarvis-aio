"""Differential noise compensation for JARVIS.

A running appliance raises the room's measured ``ambient_db``, which would push the
prosody pipeline to project louder than necessary. ``NoiseGate`` checks appliance
power signatures (``sensor.<appliance>_power``) and, when one is drawing power,
subtracts that appliance's known dB contribution from the raw reading before it
reaches prosody — so a humming dishwasher doesn't make JARVIS shout.

The dominant (loudest) running appliance sets the attenuation rather than summing,
to avoid over-correcting when several run at once. Reads the state machine but
imports no HA modules, so it stays a pure, directly-testable leaf.
"""
from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

DEFAULT_POWER_ON_W = 10.0  # watts above which an appliance counts as "running"

# sensor.<appliance>_power → dB the appliance adds to the room when running.
# Starting profile for this property's appliances; map to your actual power
# sensors. Entities that don't exist are simply ignored (no attenuation).
DEFAULT_PROFILES: dict[str, float] = {
    "sensor.dishwasher_power": 8.0,
    "sensor.washer_power": 7.0,
    "sensor.dryer_power": 9.0,
    "sensor.microwave_power": 11.0,
    "sensor.sump_pump_power": 9.0,
    "sensor.dehumidifier_power": 5.0,
}


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class NoiseGate:
    """Subtract running-appliance noise from the ambient_db before prosody."""

    def __init__(
        self,
        hass,
        profiles: dict[str, float] | None = None,
        *,
        power_on_threshold: float = DEFAULT_POWER_ON_W,
    ) -> None:
        self.hass = hass
        self.profiles = profiles if profiles is not None else DEFAULT_PROFILES
        self.threshold = power_on_threshold

    def running_appliances(self) -> list[str]:
        """Power sensors currently above the on-threshold."""
        running: list[str] = []
        for entity_id in self.profiles:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            watts = _as_float(state.state)
            if watts is not None and watts > self.threshold:
                running.append(entity_id)
        return running

    def attenuation(self) -> float:
        """dB to subtract: the dominant running appliance's contribution."""
        running = self.running_appliances()
        if not running:
            return 0.0
        return max(self.profiles[eid] for eid in running)

    def compensated_db(self, ambient_db: float | None) -> float | None:
        """Return ``ambient_db`` with running-appliance noise removed (floored at
        0). Passes None straight through (unknown stays unknown)."""
        if ambient_db is None:
            return None
        atten = self.attenuation()
        if atten <= 0.0:
            return ambient_db
        adjusted = max(0.0, ambient_db - atten)
        _LOGGER.debug(
            "noise gate: ambient_db %.1f − %.1f (appliance noise) → %.1f",
            ambient_db, atten, adjusted,
        )
        return adjusted
