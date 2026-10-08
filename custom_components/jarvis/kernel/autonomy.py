"""Graduated autonomy — per-capability trust (kernel Phase N, docs/KERNEL_PLAN.md).

Replaces the single autonomy flag with a per-capability autonomy *level* that is
EARNED on a verified track record. A capability's level is a **pure function** of
its success history (the Outcome Model's verified record) and its risk class
(``authority.sensitivity``): trust is evidence × risk, nothing more.

Ladder (least → most autonomous):
    SUGGEST  — may only propose; a human acts.
    CONFIRM  — may act only with explicit confirmation.
    ACT      — may act autonomously.

Governance invariants (from the kernel plan):
  * **Safety classes never auto-promote.** A SECURITY-class capability is pinned
    at CONFIRM no matter how clean its record — security actuation always asks, so
    a flawless streak can never earn it the right to act unattended.
  * **Earned, not granted.** A level above a class's floor requires a minimum
    *verified* sample at a minimum success rate; thin or poor evidence stays at
    the floor. SAFE reads need no evidence (pinned at ACT); SENSITIVE actuation
    earns up from SUGGEST.
  * **Bounded, reversible.** From a known current level a single evaluation moves
    at most one rung (``step_toward``) — trust climbs one step at a time and a
    lapse in the record demotes it the same way.
  * **Fail-safe = the current single setting.** This module is pure and advisory;
    nothing consumes it until the owner-gated **enforce** rung, whose fail-safe is
    the existing global autonomy flag. The enforce promotion is **owner-gated**.

Pure: no Home Assistant import, no I/O — reuses the kernel's own
``authority.sensitivity`` risk taxonomy so there is a single source of truth for a
capability's risk class.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .authority import SAFE, SECURITY, SENSITIVE, sensitivity

_LOGGER = logging.getLogger(__name__)

# ── the ladder (least → most autonomous) ──────────────────────────────────────
SUGGEST = "suggest"  # propose only; a human acts
CONFIRM = "confirm"  # act only with explicit confirmation
ACT = "act"          # act autonomously

_LADDER = (SUGGEST, CONFIRM, ACT)
_RANK = {level: i for i, level in enumerate(_LADDER)}

# ── per risk-class floor / ceiling ────────────────────────────────────────────
# A class's level is confined to [floor, ceiling]. SAFE and SECURITY are pinned
# (floor == ceiling): safe reads always ACT; security actuation always CONFIRMs
# and can NEVER auto-promote to ACT. Only SENSITIVE actuation earns up.
_FLOOR = {SAFE: ACT, SENSITIVE: SUGGEST, SECURITY: CONFIRM}
_CEILING = {SAFE: ACT, SENSITIVE: ACT, SECURITY: CONFIRM}

# ── earning thresholds (verified track record needed to climb a rung) ─────────
# Conservative defaults; observe-only at this rung, so they are a starting point
# to be reviewed before the owner-gated enforce flip, not a committed contract.
MIN_SAMPLES_CONFIRM = 5
RATE_CONFIRM = 0.6
MIN_SAMPLES_ACT = 20
RATE_ACT = 0.9


@dataclass(frozen=True)
class AutonomyGrant:
    """The autonomy a capability has earned, with the evidence behind it."""

    capability: str
    risk: str            # SAFE / SENSITIVE / SECURITY
    level: str           # SUGGEST / CONFIRM / ACT
    floor: str
    ceiling: str
    samples: int
    success_rate: float
    reason: str

    @property
    def may_act(self) -> bool:
        return self.level == ACT

    @property
    def needs_confirmation(self) -> bool:
        return self.level == CONFIRM

    @property
    def suggest_only(self) -> bool:
        return self.level == SUGGEST

    @property
    def pinned(self) -> bool:
        """True when this capability's risk class cannot earn up or down."""
        return self.floor == self.ceiling

    def to_dict(self) -> dict:
        return {
            "capability": self.capability,
            "risk": self.risk,
            "level": self.level,
            "floor": self.floor,
            "ceiling": self.ceiling,
            "samples": self.samples,
            "success_rate": round(self.success_rate, 6),
            "reason": self.reason,
        }


def _clamp(level: str, floor: str, ceiling: str) -> str:
    """Confine ``level`` to the ``[floor, ceiling]`` band on the ladder."""
    r = _RANK.get(level, 0)
    r = max(r, _RANK[floor])
    r = min(r, _RANK[ceiling])
    return _LADDER[r]


def step_toward(current: str, target: str) -> str:
    """Move ``current`` at most one rung toward ``target`` (bounded, reversible).

    Trust is earned one step at a time: a clean record promotes a single rung per
    evaluation, and a lapse demotes a single rung — never a jump."""
    c = _RANK.get(current, 0)
    t = _RANK.get(target, 0)
    if t > c:
        return _LADDER[c + 1]
    if t < c:
        return _LADDER[c - 1]
    return _LADDER[c]


def earned_level(stats, risk: str) -> str:
    """The autonomy level a capability's verified track record supports, clamped
    to its risk class's floor and ceiling. Pure and total.

    ``stats`` is any object exposing ``count`` and ``success_rate`` (an
    :class:`kernel.outcome.OutcomeStats`); ``None`` or an empty record yields the
    floor. SAFE and SECURITY classes are pinned (floor == ceiling) and ignore the
    record — only SENSITIVE actuation earns up."""
    floor = _FLOOR.get(risk, SUGGEST)
    ceiling = _CEILING.get(risk, CONFIRM)
    if floor == ceiling:  # pinned class — no earning
        return floor
    n = int(getattr(stats, "count", 0) or 0)
    rate = float(getattr(stats, "success_rate", 0.0) or 0.0)
    level = floor
    if n >= MIN_SAMPLES_CONFIRM and rate >= RATE_CONFIRM:
        level = CONFIRM
    if n >= MIN_SAMPLES_ACT and rate >= RATE_ACT:
        level = ACT
    return _clamp(level, floor, ceiling)


def grant(capability: str, stats=None, *, current: Optional[str] = None) -> AutonomyGrant:
    """The autonomy grant for ``capability`` given its track record ``stats``.

    With no ``current`` level the grant is the full earned target. With a
    ``current`` level the grant moves at most one rung toward that target
    (bounded, reversible) and is re-clamped to the risk band, so repeated
    evaluation walks the ladder one step at a time. Pure."""
    risk = sensitivity(capability)
    floor = _FLOOR.get(risk, SUGGEST)
    ceiling = _CEILING.get(risk, CONFIRM)
    target = earned_level(stats, risk)
    if current is None:
        level = target
    else:
        level = _clamp(step_toward(_clamp(current, floor, ceiling), target), floor, ceiling)
    n = int(getattr(stats, "count", 0) or 0)
    rate = float(getattr(stats, "success_rate", 0.0) or 0.0)
    if floor == ceiling:
        reason = f"{risk} capability pinned at {level} (never auto-promotes)"
    elif n == 0:
        reason = f"no verified record — holds at floor {level}"
    else:
        reason = f"{n} outcome(s) at {rate:.2f} success → earns {target} (now {level})"
    return AutonomyGrant(
        capability=capability,
        risk=risk,
        level=level,
        floor=floor,
        ceiling=ceiling,
        samples=n,
        success_rate=rate,
        reason=reason,
    )
