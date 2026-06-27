"""Telemetry-driven prosody for JARVIS announcements.

The ``ProsodyController`` turns a snapshot of room telemetry (light, noise,
media activity, alert severity) plus the current clock into a vocal profile —
volume, speech rate, a named style, and two behavioural flags. The integration
layer (``__init__.py``) gathers the telemetry from the target area's entities
and feeds it here; this module performs no Home Assistant I/O so it stays pure
and trivially unit-testable.

Precedence (highest first), matching the design spec:
    1. critical          → authoritative, full volume, ducks media (safety)
    2. deep quiet         → whisper           (quiet hours AND dark AND quiet)
    3. partial quiet      → subdued           (quiet hours, or dark+quiet)
    4. loud / media       → projected         (be heard over noise)
    5. otherwise          → neutral

Note that 3 is evaluated before 4: during quiet hours JARVIS stays subdued
rather than projecting, but if media is playing it still ducks that media so
the subdued voice remains audible.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

# ── Thresholds (all overridable via the constructor or telemetry) ─────────────
DARK_LUX: float = 5.0          # below this → "dark"
QUIET_DB: float = 45.0         # below this → "quiet room"
LOUD_DB: float = 60.0          # at/above this → "loud room" (project to be heard)

DEFAULT_QUIET_START: int = 22  # 22:00
DEFAULT_QUIET_END: int = 7     # 07:00


@dataclass(frozen=True)
class VocalProfile:
    """Structured result. ``as_dict`` gives the plain dict the spec requires."""
    volume: float
    speech_rate: float
    style: str
    whisper_mode: bool
    duck_media: bool

    def as_dict(self) -> dict[str, float | str | bool]:
        return {
            "volume": self.volume,
            "speech_rate": self.speech_rate,
            "style": self.style,
            "whisper_mode": self.whisper_mode,
            "duck_media": self.duck_media,
        }


def _as_float(value: object) -> float | None:
    """Coerce a telemetry value (which may be a HA state string like
    'unavailable') to float, or None if it isn't a usable number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # Guard against NaN/inf leaking from a flaky sensor.
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


class ProsodyController:
    """Computes a :class:`VocalProfile` from room telemetry and the clock."""

    def __init__(
        self,
        quiet_hours_start: int = DEFAULT_QUIET_START,
        quiet_hours_end: int = DEFAULT_QUIET_END,
        *,
        dark_lux: float = DARK_LUX,
        quiet_db: float = QUIET_DB,
        loud_db: float = LOUD_DB,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.quiet_hours_start = int(quiet_hours_start) % 24
        self.quiet_hours_end = int(quiet_hours_end) % 24
        self.dark_lux = dark_lux
        self.quiet_db = quiet_db
        self.loud_db = loud_db
        # Injected clock keeps quiet-hours logic deterministic under test.
        self._clock = clock or datetime.now

    # ── Quiet-hours helper ────────────────────────────────────────────────
    def _current_hour(self, telemetry: dict) -> int:
        override = telemetry.get("hour")
        if isinstance(override, int) and 0 <= override <= 23:
            return override
        return self._clock().hour

    def in_quiet_hours(self, hour: int) -> bool:
        """True if ``hour`` falls within the quiet window, handling the common
        case where the window wraps past midnight (e.g. 22 → 7)."""
        start, end = self.quiet_hours_start, self.quiet_hours_end
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        # Wraps midnight.
        return hour >= start or hour < end

    # ── Main entry point ──────────────────────────────────────────────────
    def calculate_vocal_profile(self, telemetry: dict) -> dict:
        """Return the vocal profile for the given telemetry as a plain dict.

        Recognised telemetry keys (all optional; missing values are treated as
        'unknown' and simply don't trigger their rule):
            critical_alert (bool), ambient_lux (float), ambient_db (float),
            media_active / media_playing (bool), skip_preamble (bool),
            hour (int, test override).

        When skip_preamble is True (the listener is demonstrably present and
        attending, per SpatialContextEngine), the speech rate is eased by 0.05 so
        the terse, preamble-free status reads clearly.
        """
        telemetry = telemetry or {}

        critical = bool(telemetry.get("critical_alert", False))
        lux = _as_float(telemetry.get("ambient_lux"))
        db = _as_float(telemetry.get("ambient_db"))
        media_active = bool(
            telemetry.get("media_active", telemetry.get("media_playing", False))
        )
        skip_preamble = bool(telemetry.get("skip_preamble", False))
        hour = self._current_hour(telemetry)

        quiet_hours = self.in_quiet_hours(hour)
        is_dark = lux is not None and lux < self.dark_lux
        is_quiet_room = db is not None and db < self.quiet_db
        is_loud_room = db is not None and db >= self.loud_db

        # 1. Critical overrides every contextual consideration.
        if critical:
            profile = VocalProfile(
                volume=1.0, speech_rate=1.15, style="authoritative",
                whisper_mode=False, duck_media=True,
            )
        # 2. Deep quiet → whisper.
        elif quiet_hours and is_dark and is_quiet_room:
            profile = VocalProfile(
                volume=0.25, speech_rate=0.95, style="whisper",
                whisper_mode=True, duck_media=False,
            )
        # 3. Partial quiet → subdued. Duck any media so the soft voice is heard.
        elif quiet_hours or (is_dark and is_quiet_room):
            profile = VocalProfile(
                volume=0.4, speech_rate=0.97, style="subdued",
                whisper_mode=False, duck_media=media_active,
            )
        # 4. Loud environment or active media → project to be heard.
        elif is_loud_room or media_active:
            profile = VocalProfile(
                volume=0.9, speech_rate=1.0, style="projected",
                whisper_mode=False, duck_media=True,
            )
        # 5. Default neutral delivery.
        else:
            profile = VocalProfile(
                volume=0.6, speech_rate=1.0, style="neutral",
                whisper_mode=False, duck_media=False,
            )

        result = profile.as_dict()
        if skip_preamble:
            result["speech_rate"] = round(max(0.5, result["speech_rate"] - 0.05), 2)
        return result
