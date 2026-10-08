"""Learning & adaptation (kernel primitive — roadmap Phase M).

Turns the Epistemic Fabric's structured :class:`kernel.outcome.Outcome` records
into **bounded, reversible** weight adjustments — the closed-loop update the
audit calls for, computed as plain math over outcomes rather than an
unconstrained LLM log-read.

Each adjustment nudges a prior weight toward the evidence by the mean
``learning_signal`` of the outcomes, scaled so a single adjustment can move the
weight by at most ``step_cap`` and can never leave ``[floor, ceil]``. The prior
is always retained on the :class:`WeightAdjustment`, so applying and reverting
are both pure and lossless — nothing is learned irreversibly.

PURE (Phase M, first rung): the structure and math exist and are unit-tested,
but nothing live consumes them yet. Later rungs compute would-be adjustments in
shadow, compare offline (parity), then update belief confidences from outcomes
behind ``LEARNING_ENFORCE`` (clamped + audited), with frozen weights as the
fail-safe.

**Governance invariant (enforced here, pure):** a *protected* weight — one
standing in for an authority gate or a safety threshold — can never be *relaxed*
by learning. By convention a higher weight is the safer / more-restrictive
direction, so a protected adjustment is clamped to ``delta >= 0``: learning may
tighten such a weight, never loosen it. This holds regardless of how negative the
outcomes' learning signal is.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional

from .outcome import Outcome, summarize

# Defaults: a conservative per-update step and the usual confidence range.
DEFAULT_STEP_CAP = 0.1
DEFAULT_FLOOR = 0.0
DEFAULT_CEIL = 1.0


def _clamp(x, lo: float, hi: float, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if v != v:          # NaN
        return default
    return lo if v < lo else hi if v > hi else v


@dataclass(frozen=True)
class WeightAdjustment:
    """A proposed, bounded, reversible change to one weight.

    ``prior`` is the clamped starting value, ``proposed`` the new value after a
    capped nudge toward the evidence; ``delta == proposed - prior``. ``signal`` is
    the mean learning signal that drove it and ``samples`` how many outcomes it
    rests on. ``protected`` marks an authority/safety weight that may only tighten;
    ``clamped`` is True when the raw step hit a bound (step cap, floor/ceil, or the
    protection guard)."""

    key: str
    prior: float
    proposed: float
    delta: float
    signal: float
    samples: int
    protected: bool = False
    clamped: bool = False

    @property
    def changed(self) -> bool:
        return self.proposed != self.prior

    def applied(self) -> float:
        """The weight after applying the adjustment."""
        return self.proposed

    def reverted(self) -> float:
        """The weight after undoing the adjustment — always the original prior
        (adjustments are lossless and reversible)."""
        return self.prior


def adjust(
    key: str,
    prior: float,
    outcomes: Iterable[Outcome] = (),
    *,
    step_cap: float = DEFAULT_STEP_CAP,
    floor: float = DEFAULT_FLOOR,
    ceil: float = DEFAULT_CEIL,
    protected: bool = False,
) -> WeightAdjustment:
    """A bounded, reversible adjustment of ``prior`` toward the evidence in
    ``outcomes``. Pure and total — no outcomes (or an all-junk set) yields a
    no-op adjustment. A ``protected`` weight is never relaxed (``delta >= 0``)."""
    cap = abs(float(step_cap)) if step_cap is not None else DEFAULT_STEP_CAP
    lo, hi = float(floor), float(ceil)
    if hi < lo:
        lo, hi = hi, lo
    prior_c = _clamp(prior, lo, hi, default=lo)
    stats = summarize(outcomes)
    if stats.count == 0:
        return WeightAdjustment(key, prior_c, prior_c, 0.0, 0.0, 0, protected, False)

    signal = _clamp(stats.mean_learning_signal, -1.0, 1.0)
    proposed = prior_c + signal * cap
    clamped = False
    # governance: a protected weight may only tighten (never move down)
    if protected and proposed < prior_c:
        proposed, clamped = prior_c, True
    # keep it inside the allowed range
    if proposed < lo:
        proposed, clamped = lo, True
    elif proposed > hi:
        proposed, clamped = hi, True
    # re-assert the protection floor after range clamping
    if protected and proposed < prior_c:
        proposed, clamped = prior_c, True

    return WeightAdjustment(
        key=key,
        prior=prior_c,
        proposed=proposed,
        delta=proposed - prior_c,
        signal=signal,
        samples=stats.count,
        protected=protected,
        clamped=clamped,
    )


def plan_adjustments(
    priors: Mapping[str, float],
    outcomes_by_key: Mapping[str, Iterable[Outcome]],
    *,
    step_cap: float = DEFAULT_STEP_CAP,
    floor: float = DEFAULT_FLOOR,
    ceil: float = DEFAULT_CEIL,
    protected_keys: Optional[Iterable[str]] = None,
) -> List[WeightAdjustment]:
    """Adjustments for several weights at once — one per key in ``priors`` that
    has outcomes, strongest absolute change first. Keys in ``protected_keys`` get
    the never-relax guard. Pure."""
    protected = set(protected_keys or ())
    out: List[WeightAdjustment] = []
    for key, prior in (priors or {}).items():
        adj = adjust(
            key, prior, (outcomes_by_key or {}).get(key, ()),
            step_cap=step_cap, floor=floor, ceil=ceil,
            protected=key in protected,
        )
        if adj.changed:
            out.append(adj)
    return sorted(out, key=lambda a: abs(a.delta), reverse=True)
