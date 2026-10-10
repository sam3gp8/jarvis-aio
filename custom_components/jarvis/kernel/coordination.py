"""Peer agency coordination (roadmap Phase O — merged O + AB) — pure primitive.

Phase O covers both hierarchical delegation (``kernel.agency``, already enforce)
and **peer coordination**: peers bid / claim / settle over a shared objective so a
task is split across agencies without escalation or **double-actuation**. This is
the budgeted peer protocol, pure: given the bids for an objective, exactly one
claim is awarded, bids over budget are rejected, and conflicts resolve by the
canonical :mod:`kernel.priority` ladder (a safety-tier bid outranks a convenience
one) — never by recency or bid order alone.

Pure: no Home Assistant import, no I/O, no clock, no bus — bids are injected and
arbitration/settlement are total, deterministic derivations. A live binder (the
event-bus bid/claim/settle loop) wires it on at the shadow rung; nothing live
consumes it yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from . import priority

# Claim lifecycle.
OPEN = "open"
CLAIMED = "claimed"
SETTLED = "settled"
FAILED = "failed"


def _norm(value) -> str:
    return str(value or "").strip().lower()


def _nonneg(x) -> float:
    try:
        f = float(x)
        return f if f >= 0.0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _clamp01(x) -> float:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f < 0.0 else 1.0 if f > 1.0 else f


@dataclass(frozen=True)
class Bid:
    """One agency's offer to take on an objective. ``cost`` is charged against the
    objective ``budget``; ``tier`` is the priority tier it would act under (used
    for conflict resolution); ``confidence`` breaks ties. Total — values coerced."""

    agency_id: str
    objective: str
    cost: float = 0.0
    tier: str = priority.HOUSEHOLD
    confidence: float = 0.5

    def __post_init__(self):
        object.__setattr__(self, "agency_id", _norm(self.agency_id))
        object.__setattr__(self, "objective", _norm(self.objective))
        object.__setattr__(self, "cost", _nonneg(self.cost))
        t = _norm(self.tier)
        object.__setattr__(self, "tier", t if t in priority.ORDER else priority.HOUSEHOLD)
        object.__setattr__(self, "confidence", _clamp01(self.confidence))

    def to_dict(self) -> dict:
        return {"agency_id": self.agency_id, "objective": self.objective,
                "cost": self.cost, "tier": self.tier, "confidence": self.confidence}


@dataclass(frozen=True)
class Claim:
    """The single award for an objective (or an OPEN/empty result when no bid is
    eligible). ``winner`` is the awarded agency id; ``status`` tracks the
    lifecycle."""

    objective: str
    winner: str = ""
    status: str = OPEN
    cost: float = 0.0

    @property
    def awarded(self) -> bool:
        return bool(self.winner) and self.status in (CLAIMED, SETTLED)

    def to_dict(self) -> dict:
        return {"objective": self.objective, "winner": self.winner,
                "status": self.status, "cost": self.cost}


def eligible_bids(bids, *, budget: float) -> List[Bid]:
    """Bids for which ``cost`` fits within ``budget`` — a bid that would overrun is
    rejected (budgeted protocol). Total; never raises."""
    b = _nonneg(budget)
    return [x for x in (bids or []) if isinstance(x, Bid) and x.agency_id and x.cost <= b]


def _winner_key(bid: Bid):
    # Higher priority tier first, then lower cost, then higher confidence, then a
    # stable agency_id — fully deterministic, no reliance on bid order.
    return (-priority.rank(bid.tier), bid.cost, -bid.confidence, bid.agency_id)


def arbitrate(bids, *, budget: float = 0.0) -> Claim:
    """Award the objective to **exactly one** bid: among those within ``budget``,
    the highest-priority tier wins (conflicts resolve by the priority ladder, not
    by order), tie-broken by lower cost then higher confidence. Returns an OPEN,
    winner-less Claim when nothing is eligible — so there is never a double-award.
    Total; never raises."""
    elig = eligible_bids(bids, budget=budget)
    if not elig:
        obj = ""
        for x in (bids or []):
            if isinstance(x, Bid) and x.objective:
                obj = x.objective
                break
        return Claim(objective=obj, status=OPEN)
    best = sorted(elig, key=_winner_key)[0]
    return Claim(objective=best.objective, winner=best.agency_id,
                 status=CLAIMED, cost=best.cost)


def settle(claim: Claim, *, success: bool) -> Claim:
    """Close out an awarded claim: SETTLED on success, FAILED otherwise. An
    unawarded (OPEN) claim cannot be settled and is returned unchanged. Total."""
    if not isinstance(claim, Claim) or not claim.awarded:
        return claim if isinstance(claim, Claim) else Claim(objective="")
    return Claim(objective=claim.objective, winner=claim.winner,
                 status=SETTLED if success else FAILED, cost=claim.cost)


def would_double_actuate(claims) -> bool:
    """Guard: True if more than one awarded claim exists for the *same* objective —
    the exact failure the single-award protocol prevents. Total."""
    seen = set()
    for c in (claims or []):
        if isinstance(c, Claim) and c.awarded:
            if c.objective in seen:
                return True
            seen.add(c.objective)
    return False
