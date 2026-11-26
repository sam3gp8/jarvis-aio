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
import time
from enum import IntEnum

_LOGGER = logging.getLogger(__name__)

# A lock whose holder never releases (a crash mid-service-call, a lost awaitable)
# would wedge an entity forever. A time-to-live lets the registry self-heal:
# past it, the stale token is treated as invalid and evicted on the next touch.
DEFAULT_LOCK_TTL_S = 30.0


class Priority(IntEnum):
    """Higher wins. Real-time presence beats stochastic prediction; safety beats all."""
    PREDICTIVE = 10   # stochastic habit matrix
    ROUTINE = 20      # scheduled / background automation
    INTENT = 30       # explicit user command
    VISUAL = 40       # real-time visual-presence command
    SAFETY = 50       # safety overrides everything


class LockToken:
    """Opaque handle for a held lock; ``valid`` flips False if preempted/released.
    ``ttl_s`` (None = never) bounds how long it stays live before it self-expires."""

    __slots__ = ("entity", "priority", "valid", "acquired_at", "ttl_s")

    def __init__(self, entity: str, priority: int,
                 *, ttl_s: float | None = DEFAULT_LOCK_TTL_S) -> None:
        self.entity = entity
        self.priority = priority
        self.valid = True
        self.acquired_at = time.monotonic()
        self.ttl_s = ttl_s

    def expired(self, now: float | None = None) -> bool:
        if self.ttl_s is None:
            return False
        return (time.monotonic() if now is None else now) - self.acquired_at >= self.ttl_s


class EntityLockRegistry:
    """Priority-aware mutual-exclusion locks keyed by entity_id."""

    def __init__(self) -> None:
        self._locks: dict[str, LockToken] = {}

    def _active(self, entity: str) -> LockToken | None:
        """The live holder of ``entity``, evicting a released/preempted or
        TTL-expired token so a wedged lock heals itself on the next touch."""
        current = self._locks.get(entity)
        if current is None:
            return None
        if not current.valid or current.expired():
            current.valid = False
            if self._locks.get(entity) is current:
                del self._locks[entity]
            return None
        return current

    def try_acquire(self, entity: str, priority: int,
                    *, ttl_s: float | None = DEFAULT_LOCK_TTL_S) -> LockToken | None:
        """Acquire ``entity`` at ``priority``. Returns a token, or None if a lock
        of equal-or-higher priority is already held (the incoming request is
        discarded). A strictly-higher priority preempts the current holder. A
        stale (TTL-expired) holder is treated as not held."""
        current = self._active(entity)
        if current is not None:
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
        token = LockToken(entity, int(priority), ttl_s=ttl_s)
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
        return self._active(entity) is not None

    def held_priority(self, entity: str) -> int | None:
        current = self._active(entity)
        return current.priority if current is not None else None

    @contextlib.asynccontextmanager
    async def guard(self, entity: str, priority: int,
                    *, ttl_s: float | None = DEFAULT_LOCK_TTL_S):
        """Async context manager: yields a token (or None if contended) and always
        releases on exit."""
        token = self.try_acquire(entity, priority, ttl_s=ttl_s)
        try:
            yield token
        finally:
            self.release(token)
