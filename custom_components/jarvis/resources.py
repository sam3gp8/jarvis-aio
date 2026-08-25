"""Lifecycle resource registry for a JARVIS config entry.

Collects the disposables created during setup — event-listener unsub callbacks,
background asyncio tasks, and objects with a shutdown()/close() — behind one
handle, so async_unload_entry can tear everything down with a single fail-safe
close_all(). Each item is disposed independently (one failure never blocks the
rest), and close_all() is idempotent, so a reload can't double-dispose or leak.
"""
from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)


class JarvisResources:
    """A bag of things to dispose when the entry unloads."""

    def __init__(self):
        self._unsubs: list = []       # callables returned by track/listen
        self._tasks: list = []        # asyncio tasks/handles with .cancel()
        self._closeables: list = []   # objects with .shutdown() or .close()

    def add_unsub(self, unsub) -> None:
        if callable(unsub):
            self._unsubs.append(unsub)

    def add_unsubs(self, unsubs) -> None:
        for u in (unsubs or []):
            self.add_unsub(u)

    def add_task(self, task) -> None:
        if task is not None:
            self._tasks.append(task)

    def add_closeable(self, obj) -> None:
        if obj is not None:
            self._closeables.append(obj)

    def close_all(self) -> dict:
        """Dispose everything registered, fail-safe and idempotent. Returns a
        small summary ({unsubs, tasks, closeables, errors})."""
        summary = {"unsubs": 0, "tasks": 0, "closeables": 0, "errors": 0}
        for unsub in self._unsubs:
            try:
                if callable(unsub):
                    unsub()
                    summary["unsubs"] += 1
            except Exception:
                summary["errors"] += 1
        for task in self._tasks:
            try:
                cancel = getattr(task, "cancel", None)
                if callable(cancel):
                    cancel()
                    summary["tasks"] += 1
            except Exception:
                summary["errors"] += 1
        for obj in self._closeables:
            try:
                fn = getattr(obj, "shutdown", None) or getattr(obj, "close", None)
                if callable(fn):
                    fn()
                    summary["closeables"] += 1
            except Exception:
                summary["errors"] += 1
        self._unsubs.clear()
        self._tasks.clear()
        self._closeables.clear()
        return summary
