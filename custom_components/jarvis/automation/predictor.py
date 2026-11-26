"""Stochastic predictive matrix for JARVIS.

``PredictiveHabitMatrix`` records time-stamped observations (room entries, device
activations) to a bounded on-disk log and estimates, for any key and time-of-day
bucket, the probability that the action recurs — a simple moving average over the
distinct days observed. When that probability crosses a threshold for an upcoming
window, the action is a candidate for pre-emptive execution a few minutes early.

Stdlib-only: loads and tests without Home Assistant. The integration is
responsible for feeding observations and acting on ``due_preemptions`` — and, in
keeping with JARVIS's "earn autonomy" principle, pre-emptive *execution* is
gated by the caller rather than fired blindly from this model.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import time
from pathlib import Path
from threading import Lock

try:
    from ..paths import config_path_str
except ImportError:  # standalone unit-test import
    def config_path_str(*parts: str, hass=None) -> str:
        return str((Path("/") / "config").joinpath(*parts))

_LOGGER = logging.getLogger(__name__)

DEFAULT_MATRIX_PATH = config_path_str("jarvis", "habit_matrix.json")
DEFAULT_BUCKET_MINUTES = 30
DEFAULT_THRESHOLD = 0.90
DEFAULT_LEAD_MINUTES = 7          # how far ahead "pre-emptive" looks (5–10 min)
MAX_EVENTS = 5000
# Minimum distinct observed days (of the matching weekday/weekend class) before a
# probability is trusted. Without this floor a single Day-1 event scores 1.0 and
# would pre-empt on no real evidence.
DEFAULT_MIN_DAYS = 7


def _day_key(ts: float) -> str:
    return _dt.date.fromtimestamp(ts).isoformat()


def _is_weekend(ts: float) -> bool:
    return _dt.date.fromtimestamp(ts).weekday() >= 5


def _event_is_weekend(e: dict) -> bool:
    """Weekend flag for a stored event, derived from its ts for legacy records
    written before the flag existed."""
    w = e.get("weekend")
    if isinstance(w, bool):
        return w
    try:
        return _is_weekend(float(e.get("ts")))
    except (TypeError, ValueError):
        return False


class PredictiveHabitMatrix:
    """A bounded, persisted observation log with time-bucketed recurrence
    probabilities."""

    def __init__(
        self,
        path: str = DEFAULT_MATRIX_PATH,
        *,
        bucket_minutes: int = DEFAULT_BUCKET_MINUTES,
        max_events: int = MAX_EVENTS,
    ) -> None:
        self.path = path
        self.bucket_minutes = max(1, int(bucket_minutes))
        self.max_events = max_events
        self._lock = Lock()

    # ── Time bucketing ────────────────────────────────────────────────────
    def _slot(self, ts: float) -> int:
        """Index of the time-of-day bucket containing ``ts``."""
        local = _dt.datetime.fromtimestamp(ts)
        minute_of_day = local.hour * 60 + local.minute
        return minute_of_day // self.bucket_minutes

    # ── Persistence (guarded, corruption-tolerant) ────────────────────────
    def _load(self) -> list[dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except FileNotFoundError:
            return []
        except (json.JSONDecodeError, OSError) as exc:
            _LOGGER.warning("habit matrix unreadable (%s) — starting fresh", exc)
            return []

    def _save(self, events: list[dict]) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(events, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError as exc:
            _LOGGER.error("Failed to persist habit matrix: %s", exc)

    # ── Recording ─────────────────────────────────────────────────────────
    def record_event(self, key: str, ts: float | None = None) -> dict:
        """Record that ``key`` (e.g. 'office_entry' or 'office_hvac_on')
        occurred at ``ts`` (defaults to now)."""
        when = time.time() if ts is None else float(ts)
        event = {"key": str(key), "ts": when, "slot": self._slot(when),
                 "day": _day_key(when), "weekend": _is_weekend(when)}
        with self._lock:
            events = self._load()
            events.append(event)
            if len(events) > self.max_events:
                events = events[-self.max_events:]
            self._save(events)
        return event

    # ── Probability ───────────────────────────────────────────────────────
    def _probability_from_events(
        self, events: list[dict], key: str, at_ts: float, *,
        min_days: int = DEFAULT_MIN_DAYS,
    ) -> float:
        """Pure probability over an ALREADY-LOADED event list, so a batch caller
        (``due_preemptions``) reads the matrix once instead of once per key.

        Scopes to the weekday/weekend class of ``at_ts`` — a Saturday habit is not
        evidence for a Tuesday — and returns 0.0 until at least ``min_days``
        distinct days of that class have been observed, so a single Day-1 event
        can't score 1.0 and pre-empt on no real evidence."""
        if not events:
            return 0.0
        slot = self._slot(at_ts)
        want_weekend = _is_weekend(at_ts)
        scoped = [e for e in events if _event_is_weekend(e) == want_weekend]
        observed_days = {e.get("day") for e in scoped if e.get("day")}
        if len(observed_days) < max(0, int(min_days)) or not observed_days:
            return 0.0
        key_days = {
            e.get("day")
            for e in scoped
            if e.get("key") == key and e.get("slot") == slot and e.get("day")
        }
        return round(len(key_days) / len(observed_days), 4)

    def probability(
        self, key: str, at_ts: float, *, min_days: int = DEFAULT_MIN_DAYS
    ) -> float:
        """P(key occurs in the time bucket containing ``at_ts``), as the share of
        distinct observed days of the same weekday/weekend class on which it did.
        0.0 with no history or fewer than ``min_days`` observed days of that class."""
        with self._lock:
            events = self._load()
        return self._probability_from_events(events, key, at_ts, min_days=min_days)

    def should_preempt(
        self, key: str, at_ts: float, *, threshold: float = DEFAULT_THRESHOLD,
        min_days: int = DEFAULT_MIN_DAYS,
    ) -> bool:
        """True if ``key``'s recurrence probability for ``at_ts``'s bucket meets
        the confidence threshold."""
        return self.probability(key, at_ts, min_days=min_days) >= threshold

    def due_preemptions(
        self,
        now_ts: float | None = None,
        *,
        lead_minutes: int = DEFAULT_LEAD_MINUTES,
        threshold: float = DEFAULT_THRESHOLD,
        min_days: int = DEFAULT_MIN_DAYS,
    ) -> list[dict]:
        """Keys whose probability in the bucket ``lead_minutes`` ahead meets the
        threshold — i.e. actions worth taking a few minutes early. Returns a list
        of {"key", "probability", "lead_minutes"}. Reads the matrix ONCE for all
        keys (no N+1 disk reads on the event loop)."""
        now = time.time() if now_ts is None else float(now_ts)
        target = now + lead_minutes * 60
        with self._lock:
            events = self._load()
        keys = {e.get("key") for e in events if e.get("key")}
        out: list[dict] = []
        for key in keys:
            prob = self._probability_from_events(events, key, target, min_days=min_days)
            if prob >= threshold:
                out.append({"key": key, "probability": prob, "lead_minutes": lead_minutes})
        out.sort(key=lambda d: d["probability"], reverse=True)
        return out

    def observed_days(self) -> int:
        with self._lock:
            events = self._load()
        return len({e.get("day") for e in events if e.get("day")})
