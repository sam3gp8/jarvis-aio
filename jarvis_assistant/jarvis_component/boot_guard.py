"""Boot guard primitives for JARVIS.

``AlertBuffer`` holds jarvis.speak requests that arrive before the integration is
fully initialised (or during a config-entry reload) and replays them, in order,
once readiness is declared. Stdlib-only (asyncio), so it loads and tests without
Home Assistant; the dispatch of a buffered alert is injected as a callback.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

_LOGGER = logging.getLogger(__name__)

DEFAULT_MAX = 25


class AlertBuffer:
    """A bounded FIFO of buffered alerts plus a readiness flag."""

    def __init__(self, maxsize: int = DEFAULT_MAX) -> None:
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.ready = False

    def begin(self) -> None:
        """Re-gate (e.g. on reload): mark not-ready. Buffered alerts are kept so
        anything captured during the reload window still replays."""
        self.ready = False

    def enqueue(self, data: dict) -> None:
        """Buffer one alert. If the buffer is full, drop the oldest to keep the
        most recent (alerts are most useful fresh)."""
        try:
            self._queue.put_nowait(dict(data))
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(dict(data))
                _LOGGER.warning("Alert buffer full — dropped oldest buffered alert")
            except Exception:  # noqa: BLE001
                pass

    async def mark_ready(
        self, dispatch: Callable[[dict], Awaitable[None]]
    ) -> int:
        """Flip to ready and replay buffered alerts in arrival order via
        ``dispatch``. Returns the number replayed. Idempotent."""
        if self.ready:
            return 0
        self.ready = True
        replayed = 0
        while not self._queue.empty():
            try:
                data = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                await dispatch(data)
                replayed += 1
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed replaying a buffered alert")
        return replayed

    def pending(self) -> int:
        return self._queue.qsize()
