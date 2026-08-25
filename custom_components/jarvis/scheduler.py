"""Central scheduler for JARVIS's periodic background tasks.

One place owns the recurring "ticks" (package sweep, health sweep, hazard sweep,
document ingest, …). Each task is named and tracked — last run, last success,
duration, run/error counts — so diagnostics can show whether a sweep is actually
firing, and a task that starts failing becomes visible instead of silent.

Registration goes through Home Assistant's async_track_time_interval; the unsub
handles are held here, so shutdown() tears every timer down in one call
(reload-safe). The tracker is injectable, so the logic is unit-testable without
Home Assistant.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Callable, Optional

_LOGGER = logging.getLogger(__name__)


class _Task:
    __slots__ = ("name", "interval_s", "cb", "unsub", "last_run", "last_success",
                 "last_duration_ms", "run_count", "error_count",
                 "consecutive_errors", "last_error")

    def __init__(self, name: str, interval_s: float, cb):
        self.name = name
        self.interval_s = interval_s
        self.cb = cb
        self.unsub = None
        self.last_run = 0.0
        self.last_success = 0.0
        self.last_duration_ms = 0
        self.run_count = 0
        self.error_count = 0
        self.consecutive_errors = 0
        self.last_error = ""


class JarvisScheduler:
    """Owns the periodic tasks for one config entry."""

    def __init__(self, hass=None, tracker: Optional[Callable] = None):
        self._hass = hass
        self._tasks: dict = {}
        # tracker(hass, callback, interval_timedelta) -> unsub. Defaults to HA's
        # async_track_time_interval; overridable for tests.
        self._tracker = tracker

    def _default_tracker(self, hass, cb, interval):
        from homeassistant.helpers.event import async_track_time_interval
        return async_track_time_interval(hass, cb, interval)

    def add(self, name: str, interval, cb) -> bool:
        """Register a periodic async task. ``interval`` is seconds (int/float) or
        a timedelta. A name already registered is skipped (reload-safe against
        double registration). Returns True if the timer was registered."""
        if name in self._tasks:
            _LOGGER.debug("scheduler: task '%s' already registered; skipping", name)
            return False
        secs = (interval.total_seconds()
                if isinstance(interval, timedelta) else float(interval))
        task = _Task(name, secs, cb)

        async def _wrapped(now=None):
            await self._run(task, now)

        tracker = self._tracker or self._default_tracker
        try:
            task.unsub = tracker(self._hass, _wrapped, timedelta(seconds=secs))
        except Exception as exc:
            _LOGGER.warning("scheduler: could not register '%s': %s", name, exc)
            return False
        self._tasks[name] = task
        return True

    async def _run(self, task: _Task, now=None) -> None:
        """Run one tick, recording timing and outcome. A failing tick is logged
        and counted, never propagated (an errored sweep can't crash the timer)."""
        start = time.time()
        task.last_run = start
        task.run_count += 1
        try:
            await task.cb(now)
            task.last_success = time.time()
            task.consecutive_errors = 0
            task.last_error = ""
        except Exception as exc:
            task.error_count += 1
            task.consecutive_errors += 1
            task.last_error = f"{type(exc).__name__}: {exc}"
            _LOGGER.warning("scheduler: task '%s' failed (%dx in a row): %s",
                            task.name, task.consecutive_errors, exc)
        finally:
            task.last_duration_ms = int((time.time() - start) * 1000)

    def status(self) -> list:
        """A snapshot of every task, for diagnostics."""
        out = []
        for t in self._tasks.values():
            out.append({
                "name": t.name,
                "interval_s": int(t.interval_s),
                "runs": t.run_count,
                "errors": t.error_count,
                "consecutive_errors": t.consecutive_errors,
                "last_run": t.last_run or None,
                "last_success": t.last_success or None,
                "last_duration_ms": t.last_duration_ms,
                "last_error": t.last_error,
                "healthy": t.consecutive_errors == 0,
            })
        return out

    def task_names(self) -> list:
        return list(self._tasks.keys())

    def shutdown(self) -> int:
        """Cancel every timer. Fail-safe and idempotent (safe on reload).
        Returns the number of timers torn down."""
        n = 0
        for t in self._tasks.values():
            if t.unsub:
                try:
                    t.unsub()
                    n += 1
                except Exception:
                    pass
                t.unsub = None
        self._tasks.clear()
        return n
