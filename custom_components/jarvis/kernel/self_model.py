"""Self model — JARVIS's honest, inspectable picture of itself (roadmap Phase S).

An explicit, **read-only** projection of what JARVIS *is and can do right now*:
its capabilities (each with a status), its current commitments (the goals and
open situations it is pursuing, as agency_state records them), an overall
operational confidence, and its known limits. It exists so that "what can you
do / what are you doing / are you sure?" answers can be sourced from a real
model instead of being confabulated.

It lands **pure**: a record plus deterministic derivations (``can`` / ``available``
/ ``report`` / ``to_dict``) and a total builder, with every input passed in —
nothing here reads live state, imports Home Assistant, or raises. Ladder from
here: shadow (a diagnostics read logs the projection) → parity (self-report vs
ground truth) → enforce (self-answers are sourced from the model, no
confabulation), owner-gated behind ``SELF_MODEL_ENFORCE`` with a static
capability list as the fail-safe.

**Hard rule, by construction:** this model only *describes*. It carries no
authority and has no method that grants, activates, or widens anything — naming
a capability here never makes it callable. Capability authority lives solely in
the ``authority`` primitive / the enforcement registry; the self model reads
*from* that world, it can never write *to* it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Tuple

# Capability availability, best → worst.
AVAILABLE = "available"       # ready to use now
DEGRADED = "degraded"         # usable but impaired (e.g. a provider is slow/offline)
UNAVAILABLE = "unavailable"   # present in principle but not usable right now

_STATUSES = (AVAILABLE, DEGRADED, UNAVAILABLE)


def _clamp01(x, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


@dataclass(frozen=True)
class Capability:
    """One thing JARVIS can do, with an honest status. Read-only — naming a
    capability here never grants it (authority lives in the authority primitive)."""

    name: str
    status: str = AVAILABLE
    note: str = ""

    @property
    def usable(self) -> bool:
        """Whether it can actually be used now (available or degraded, not down)."""
        return self.status in (AVAILABLE, DEGRADED)

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status,
                "usable": self.usable, "note": self.note}


@dataclass(frozen=True)
class SelfModel:
    """A read-only projection of JARVIS's current self: capabilities, commitments,
    confidence and limits. Pure — every derivation is a function of these fields,
    and none of them grants or changes anything."""

    capabilities: Tuple[Capability, ...] = ()
    commitments: Tuple[str, ...] = ()     # active goal / open situation labels
    confidence: float = 1.0               # overall operational confidence, [0, 1]
    limits: Tuple[str, ...] = ()          # known limitations, honestly stated
    identity: str = ""                    # who JARVIS is / is serving, when known

    def can(self, name: str) -> bool:
        """Whether JARVIS *reports* it can do ``name`` now — a usable capability of
        that name is present. A pure lookup that grants nothing."""
        key = (name or "").strip().lower()
        if not key:
            return False
        return any(c.name.strip().lower() == key and c.usable
                   for c in self.capabilities)

    def capability(self, name: str) -> Optional[Capability]:
        """The named capability (any status), or None — pure lookup."""
        key = (name or "").strip().lower()
        for c in self.capabilities:
            if c.name.strip().lower() == key:
                return c
        return None

    @property
    def available(self) -> Tuple[Capability, ...]:
        """The capabilities that are usable right now."""
        return tuple(c for c in self.capabilities if c.usable)

    @property
    def is_empty(self) -> bool:
        return not (self.capabilities or self.commitments or self.limits
                    or self.identity)

    def report(self) -> str:
        """An honest one-line self-report: only *usable* capabilities are counted
        as such, commitments and known limits are surfaced, and confidence is
        stated. Never over-claims — it cannot report a capability it does not
        hold, and a present-but-unavailable one is not counted usable."""
        n_ok = len(self.available)
        n_all = len(self.capabilities)
        parts = [f"{n_ok}/{n_all} capabilit{'y' if n_all == 1 else 'ies'} usable"]
        if self.commitments:
            parts.append(f"{len(self.commitments)} commitment"
                         f"{'' if len(self.commitments) == 1 else 's'}")
        if self.limits:
            parts.append(f"{len(self.limits)} known limit"
                         f"{'' if len(self.limits) == 1 else 's'}")
        parts.append(f"confidence {self.confidence:.2f}")
        who = f" · serving {self.identity}" if self.identity else ""
        return "self: " + " · ".join(parts) + who

    def to_dict(self) -> dict:
        return {
            "capabilities": [c.to_dict() for c in self.capabilities],
            "commitments": list(self.commitments),
            "confidence": round(self.confidence, 4),
            "limits": list(self.limits),
            "identity": self.identity,
            "usable_count": len(self.available),
        }


def _as_capability(x) -> Optional[Capability]:
    """Coerce a Capability / {name,status,note} mapping / bare name string into a
    Capability, or None when there is nothing usable to make one from."""
    if isinstance(x, Capability):
        return x
    if isinstance(x, Mapping):
        name = str(x.get("name", "") or "").strip()
        if not name:
            return None
        status = str(x.get("status", AVAILABLE) or AVAILABLE)
        if status not in _STATUSES:
            status = AVAILABLE
        return Capability(name=name, status=status,
                          note=str(x.get("note", "") or ""))
    if isinstance(x, str) and x.strip():
        return Capability(name=x.strip())
    return None


def project(
    *,
    capabilities: Iterable = (),
    commitments: Iterable = (),
    confidence: float = 1.0,
    limits: Iterable = (),
    identity: str = "",
) -> SelfModel:
    """Build a :class:`SelfModel` from loosely-typed inputs. Pure and total:
    ``capabilities`` may be Capability objects, ``{name, status, note}`` mappings,
    or bare name strings; malformed entries are dropped rather than raising. The
    result only *describes* — it grants nothing and carries no authority."""
    caps = tuple(c for c in (_as_capability(x) for x in (capabilities or ())) if c)
    comms = tuple(str(c).strip() for c in (commitments or ()) if str(c).strip())
    lims = tuple(str(limit).strip() for limit in (limits or ()) if str(limit).strip())
    return SelfModel(
        capabilities=caps,
        commitments=comms,
        confidence=_clamp01(confidence, 1.0),
        limits=lims,
        identity=str(identity or "").strip(),
    )
