"""Entity concurrency control for JARVIS.

``EntityLockRegistry`` enforces mutual exclusion on hardware entities so two
commands can't drive the same device at once, with a priority ladder: a
real-time visual-presence command preempts a lower-priority predictive one, while
an equal-or-lower priority request is discarded while a lock is held.

Locks are held across the awaited service call but the registry operations are
synchronous dict mutations — safe on Home Assistant's single-threaded event loop,
where no other coroutine runs between awaits. Stdlib-only, so it tests directly.

Typical use:
    reg = EntityLockRegistry()
    async with reg.guard("lock.front_door", Priority.INTENT) as token:
        if token is None:
            return                      # a higher-priority command holds it
        await hass.services.async_call(...)
"""
from __future__ import annotations

import contextlib
import logging
from enum import IntEnum

_LOGGER = logging.getLogger(__name__)


class Priority(IntEnum):
    """Higher wins. Real-time presence beats stochastic prediction; safety beats all."""
    PREDICTIVE = 10   # stochastic habit matrix
    ROUTINE = 20      # scheduled / background automation
    INTENT = 30       # explicit user command
    VISUAL = 40       # real-time visual-presence command
    SAFETY = 50       # safety overrides everything


class LockToken:
    """Opaque handle for a held lock; ``valid`` flips False if preempted/released."""

    __slots__ = ("entity", "priority", "valid")

    def __init__(self, entity: str, priority: int) -> None:
        self.entity = entity
        self.priority = priority
        self.valid = True


class EntityLockRegistry:
    """Priority-aware mutual-exclusion locks keyed by entity_id."""

    def __init__(self) -> None:
        self._locks: dict[str, LockToken] = {}

    def try_acquire(self, entity: str, priority: int) -> LockToken | None:
        """Acquire ``entity`` at ``priority``. Returns a token, or None if a lock
        of equal-or-higher priority is already held (the incoming request is
        discarded). A strictly-higher priority preempts the current holder."""
        current = self._locks.get(entity)
        if current is not None and current.valid:
            if int(priority) > current.priority:
                current.valid = False  # preempt the lower-priority holder
                _LOGGER.debug(
                    "mutex: %s preempted (held %d < incoming %d)",
                    entity, current.priority, int(priority),
                )
            else:
                _LOGGER.debug(
                    "mutex: %s busy (held %d ≥ incoming %d) — discarding",
                    entity, current.priority, int(priority),
                )
                return None
        token = LockToken(entity, int(priority))
        self._locks[entity] = token
        return token

    def release(self, token: LockToken | None) -> None:
        """Release a token. Only the current holder clears the lock; a token that
        was already preempted just goes invalid without disturbing the new holder."""
        if token is None:
            return
        current = self._locks.get(token.entity)
        token.valid = False
        if current is token:
            del self._locks[token.entity]

    def is_locked(self, entity: str) -> bool:
        current = self._locks.get(entity)
        return current is not None and current.valid

    def held_priority(self, entity: str) -> int | None:
        current = self._locks.get(entity)
        return current.priority if (current is not None and current.valid) else None

    @contextlib.asynccontextmanager
    async def guard(self, entity: str, priority: int):
        """Async context manager: yields a token (or None if contended) and always
        releases on exit."""
        token = self.try_acquire(entity, priority)
        try:
            yield token
        finally:
            self.release(token)
