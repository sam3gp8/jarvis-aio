"""Agency orchestration — hierarchical delegation (kernel Phase O, docs/KERNEL_PLAN.md).

A parent agency may spawn a CHILD agency carrying a strictly NARROWER capability
set, a bounded scope, and a delegation depth, then drive it through a small
lifecycle (pending → active → settled, or failed). The governing invariant —
**no escalation by delegation** — is the authority ``CapabilityToken``'s own rule
(a derived token only ever *intersects* its parent's capabilities), lifted here to
a named, depth-tracked, lifecycle-bearing sub-agent so FRIDAY/HOMER-style
delegation can be reasoned about and bounded.

Pure: no Home Assistant import, no I/O. Reuses ``authority.CapabilityToken`` for
the capability substrate — one source of truth for "what a sub-agent may do" and
its narrowing — and mirrors ``budget.max_delegation_depth`` for chain depth, so
there is no second, drifting notion of either. Observe-only until the owner-gated
``AGENCY_ORCHESTRATION_ENFORCE`` rung, whose fail-safe is the parent acting
directly.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, Optional

from .authority import CapabilityToken

# ── lifecycle states ──────────────────────────────────────────────────────────
PENDING = "pending"   # spawned, not yet started
ACTIVE = "active"     # running
SETTLED = "settled"   # completed and reconciled
FAILED = "failed"     # abandoned / errored

_TERMINAL: FrozenSet[str] = frozenset({SETTLED, FAILED})
# legal forward transitions; a terminal state goes nowhere.
_NEXT = {
    PENDING: frozenset({ACTIVE, FAILED}),
    ACTIVE: frozenset({SETTLED, FAILED}),
    SETTLED: frozenset(),
    FAILED: frozenset(),
}

DEFAULT_MAX_DEPTH = 3   # mirrors budget.BudgetLimits.max_delegation_depth


@dataclass(frozen=True)
class Agency:
    """A (sub-)agency: who it is, the capability token bounding what it may do, how
    deep in the delegation chain it sits, its scope/objective, and its lifecycle
    state. Immutable — transitions return a new instance."""

    holder: str
    token: CapabilityToken
    depth: int = 0
    parent: Optional[str] = None
    scope: Optional[str] = None
    objective: Optional[str] = None
    state: str = PENDING
    agency_id: str = field(default_factory=lambda: "agy_" + uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)

    @property
    def capabilities(self) -> FrozenSet[str]:
        return self.token.capabilities

    @property
    def terminal(self) -> bool:
        return self.state in _TERMINAL

    def permits(self, capability: str) -> bool:
        """Whether this agency's token grants ``capability`` — the no-escalation
        check: a child only ever holds capabilities it was delegated."""
        return self.token.grants(capability)


@dataclass(frozen=True)
class SpawnCheck:
    """The pure verdict of whether (and how narrowed) a child spawn is legal."""

    ok: bool
    reason: str
    granted: FrozenSet[str]   # capabilities the child would actually hold
    dropped: FrozenSet[str]   # requested but NOT held by the parent (narrowed away)
    depth: int

    def __bool__(self) -> bool:
        return self.ok


def root(holder: str, capabilities: Iterable[str], *,
         scope: Optional[str] = None, now: Optional[float] = None) -> Agency:
    """A top-level agency (e.g. JARVIS itself, holding ``{"*"}``). Starts ACTIVE."""
    tok = CapabilityToken(holder=holder, capabilities=frozenset(capabilities),
                          scope=scope)
    kw = {} if now is None else {"created_at": now}
    return Agency(holder=holder, token=tok, depth=0, parent=None, scope=scope,
                  state=ACTIVE, **kw)


def can_spawn(parent: Agency, capabilities: Iterable[str], *,
              max_depth: int = DEFAULT_MAX_DEPTH) -> SpawnCheck:
    """Whether ``parent`` may delegate ``capabilities`` to a child, and how the
    request narrows against what the parent holds. Pure and total.

    A spawn is refused if the parent is terminal, if the resulting depth would
    exceed ``max_depth`` (0/negative = unlimited), or if none of the requested
    capabilities are held by the parent. Capabilities the parent lacks are
    reported in ``dropped`` — they are silently narrowed away, never granted."""
    requested = frozenset(capabilities)
    depth = parent.depth + 1
    held = parent.token.capabilities
    if "*" in held:
        granted, dropped = requested, frozenset()
    else:
        granted, dropped = (requested & held), (requested - held)
    if parent.terminal:
        return SpawnCheck(False, f"parent agency is {parent.state}",
                          granted, dropped, depth)
    if max_depth and max_depth > 0 and depth > max_depth:
        return SpawnCheck(False, f"delegation depth {depth} exceeds {max_depth}",
                          granted, dropped, depth)
    if not granted:
        return SpawnCheck(False, "no requested capability is held by the parent",
                          granted, dropped, depth)
    return SpawnCheck(True, "ok", granted, dropped, depth)


def spawn(parent: Agency, holder: str, capabilities: Iterable[str], *,
          scope: Optional[str] = None, objective: Optional[str] = None,
          ttl: Optional[float] = None, now: Optional[float] = None) -> Agency:
    """Derive a PENDING child agency from ``parent``. Its token is the parent's
    ``derive``d token (capabilities intersected with the parent's — delegation
    narrows, never widens), its depth is ``parent.depth + 1``, and its scope falls
    back to the parent's. Pure; does not itself enforce depth — call
    :func:`can_spawn` first for the legality verdict."""
    child_token = parent.token.derive(holder, capabilities, scope=scope,
                                      ttl=ttl, now=now)
    kw = {} if now is None else {"created_at": now}
    return Agency(holder=holder, token=child_token, depth=parent.depth + 1,
                  parent=parent.holder, scope=scope or parent.scope,
                  objective=objective, state=PENDING, **kw)


def can_transition(state: str, target: str) -> bool:
    """Whether ``state`` → ``target`` is a legal lifecycle move."""
    return target in _NEXT.get(state, frozenset())


def transition(agency: Agency, target: str) -> Agency:
    """A new Agency in ``target`` state. Raises ValueError on an illegal move
    (e.g. reviving a settled agency), so a caller can't fabricate a lifecycle."""
    if not can_transition(agency.state, target):
        raise ValueError(f"illegal agency transition {agency.state} -> {target}")
    return replace_state(agency, target)


def replace_state(agency: Agency, state: str) -> Agency:
    """Internal: clone ``agency`` with a new ``state`` (keeps identity + token)."""
    return Agency(holder=agency.holder, token=agency.token, depth=agency.depth,
                  parent=agency.parent, scope=agency.scope,
                  objective=agency.objective, state=state,
                  agency_id=agency.agency_id, created_at=agency.created_at)
