"""Ambient acting-agent identity (MCU Phase H, H6 — delegation attribution).

Every actuation carries an ``actor`` — *who* is acting (jarvis / friday / homer /
user). On the direct path that is JARVIS itself, so ``ActuatorRequest.actor`` and
``from_actuation(actor=...)`` default to ``"jarvis"`` and nothing has to say so.

But when JARVIS delegates an objective to a sub-agent (FRIDAY the background
automator), the actuations the sub-agent performs flow through the very same
universal seam (``control_device`` / ``bulk_control`` / ``run_scene_or_script``,
H1–H3) — so without help the journal records *"jarvis did X"* for an action
FRIDAY took under JARVIS's delegation. The audit's canonical request shape wants
*who* to be truthful.

Rather than thread an ``actor`` argument through delegate → run_agent → tool
dispatch → actuation.request, the delegating call sets the *current* acting agent
for the extent of the sub-agent's run, and ``actuation.request`` / ``emit_event``
read it as the default. The parent correlation id already threads through the
kernel correlation contextvar, so the pair reconstructs *"FRIDAY did X, correlated
to JARVIS's delegating turn"* with no call-site churn on the seam.

Backed by a :class:`contextvars.ContextVar`, so the value is isolated per async
task / thread and never leaks between concurrent chains. Pure stdlib; no Home
Assistant import.

Typical use (wrapping a delegated sub-agent run)::

    with actor.scope("friday"):
        await run_agent(...)         # any actuation inside is attributed to friday
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Iterator

DEFAULT = "jarvis"

_CURRENT: ContextVar[str] = ContextVar("jarvis_actor", default=DEFAULT)


def current() -> str:
    """The acting agent in effect for the current context (``"jarvis"`` by
    default — never None, so callers can use it unconditionally)."""
    try:
        return _CURRENT.get() or DEFAULT
    except Exception:   # pragma: no cover - defensive
        return DEFAULT


def set_current(name: str):
    """Set the current acting agent. Returns the contextvars Token for reset.

    Prefer :func:`scope` where the extent is a block; use this directly only when
    the set and reset can't be bracketed.
    """
    return _CURRENT.set(str(name or DEFAULT))


def reset(token) -> None:
    """Restore the acting agent to what it was before :func:`set_current`."""
    try:
        _CURRENT.reset(token)
    except Exception:   # pragma: no cover - defensive
        pass


@contextlib.contextmanager
def scope(name: str) -> Iterator[str]:
    """Context manager that sets the acting agent for the enclosed block.

    A falsy ``name`` is a no-op passthrough (keeps whatever is current), so callers
    can wrap unconditionally without clobbering an outer attribution.
    """
    if not name:
        yield current()
        return
    token = _CURRENT.set(str(name))
    try:
        yield str(name)
    finally:
        try:
            _CURRENT.reset(token)
        except Exception:   # pragma: no cover - defensive
            pass
