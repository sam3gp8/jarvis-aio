"""Hand-rolled fakes for unit-testing JARVIS modules without a Home Assistant
runtime. These implement only the narrow surface the modules under test actually
call — keeping them fast, deterministic, and easy to keep faithful.

Pure Python; no Home Assistant import required.
"""
from __future__ import annotations

import types


class FakeState:
    """Mirrors the handful of attributes the cores read off a hass state."""
    __slots__ = ("entity_id", "state", "attributes")

    def __init__(self, entity_id: str, state, attributes: dict | None = None):
        self.entity_id = entity_id
        self.state = state
        self.attributes = attributes or {}

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"<FakeState {self.entity_id}={self.state!r} {self.attributes}>"


class FakeStates:
    """Implements the slice of hass.states the cores use: set/get/remove and
    async_all(domain) with the same domain-prefix semantics as Home Assistant."""

    def __init__(self):
        self._d: dict[str, FakeState] = {}

    def set(self, entity_id: str, state, **attributes) -> FakeState:
        st = FakeState(entity_id, state, attributes)
        self._d[entity_id] = st
        return st

    def remove(self, entity_id: str) -> None:
        self._d.pop(entity_id, None)

    def get(self, entity_id: str):
        return self._d.get(entity_id)

    def async_all(self, domain: str | None = None):
        values = list(self._d.values())
        if domain is None:
            return values
        prefix = domain + "."
        return [s for s in values if s.entity_id.startswith(prefix)]


class _Services:
    def __init__(self, sink: list):
        self._sink = sink

    async def async_call(self, domain, service, data=None, blocking=False, **kwargs):
        # Record the intent instead of executing it, so tests can assert
        # "JARVIS tried to lock the door" without touching real devices.
        self._sink.append((domain, service, dict(data or {})))


class _Bus:
    def async_listen(self, *args, **kwargs):
        return lambda: None  # returns an unsubscribe callable, like HA


class FakeHass:
    """A fake Home Assistant core exposing only the surfaces enumerated from
    cognitive_core.py and reasoning_loop.py:

        states.async_all / states.get / states.set(test helper)
        services.async_call           (recorded into .service_calls)
        async_add_executor_job        (runs the callable synchronously)
        async_create_task / async_create_background_task  (collected; drain())
        bus.async_listen
        data                          (plain dict)
    """

    def __init__(self):
        self.states = FakeStates()
        self.data: dict = {}
        self.service_calls: list = []
        self._tasks: list = []
        self.bus = _Bus()
        self.config = types.SimpleNamespace(time_zone="America/New_York")

    @property
    def services(self):
        return _Services(self.service_calls)

    async def async_add_executor_job(self, func, *args):
        # The caller awaits this; running synchronously is deterministic and
        # avoids a real thread pool.
        return func(*args)

    def async_create_task(self, coro, name=None):
        self._tasks.append(coro)
        return coro

    def async_create_background_task(self, coro, name=None):
        self._tasks.append(coro)
        return coro

    async def drain(self):
        """Run any coroutines that were spawned via create_task, so tests can
        exercise (or simply close) the announcement side-effects."""
        pending, self._tasks = self._tasks, []
        for coro in pending:
            await coro

    def close_pending(self):
        """Close collected coroutines without running them (avoids
        'coroutine was never awaited' warnings when the effect is irrelevant)."""
        for coro in self._tasks:
            coro.close()
        self._tasks = []


class FakeProvider:
    """The single network seam for reasoning_loop.decide().

    provider.chat is synchronous and returns {"text": <str>} on success. Pass
    `exc` to simulate a failure; use a non-transient message so decide() fails
    fast without retry/backoff sleeps.
    """

    def __init__(self, replies: list[str] | None = None, exc: BaseException | None = None):
        self.replies = list(replies or [])
        self.exc = exc
        self.calls = 0
        self.last_messages = None

    def chat(self, messages, temperature=0.4, max_tokens=200, **kwargs):
        self.calls += 1
        self.last_messages = messages
        if self.exc is not None:
            raise self.exc
        text = self.replies.pop(0) if self.replies else '{"speak": false, "reason": "routine"}'
        return {"text": text, "tool_calls": [], "raw": None}
