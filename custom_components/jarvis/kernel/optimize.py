"""Self-optimization — measure, then tune within owner bounds (roadmap Phase Y).

JARVIS can measure its own performance (latency, model spend via
`kernel.token_telemetry`, cache hit-rate, interruption cost, accuracy) and, in
principle, tune itself to run better. The danger is obvious: a system that can
rewrite its own parameters must never be able to relax a safety threshold or an
authority gate in the name of "efficiency". Phase Y encodes that as a **tiered
guardrail**, structurally, in the primitive itself:

- **SAFE** — latency, cost, provider selection, cache, context size, resource
  allocation. Self-tunable within owner-set bounds.
- **SENSITIVE** — autonomy levels, confidence thresholds, interrupt thresholds,
  model selection for *safety* decisions. Proposed only; owner-gated; never
  auto-applied.
- **FORBIDDEN** — authority ceiling, security policy, identity requirements,
  safety thresholds, human override, audit retention. Never tunable by Y at all;
  a proposal to touch one is rejected outright.

Fail-safe: a parameter this module does not explicitly recognise is treated as
**FORBIDDEN** — the optimizer refuses to touch anything it cannot prove is safe,
so the default is "fixed config". Pure: no Home Assistant import and no I/O;
metrics and candidate values are injected, so the classification, bounds and
guard logic are deterministic and unit-testable. This primitive *proposes*; it
never applies anything itself.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, Optional, Tuple

# ── tunability tiers ──────────────────────────────────────────────────────────
SAFE = "safe"            # self-tunable within bounds
SENSITIVE = "sensitive"  # propose only, owner-gated
FORBIDDEN = "forbidden"  # never tunable by Y

# Known parameters → tier. Anything NOT listed defaults to FORBIDDEN (fail-safe).
_PARAM_TIER: Dict[str, str] = {
    # safe to self-optimize
    "latency": SAFE,
    "cost": SAFE,
    "model_spend": SAFE,
    "provider_selection": SAFE,
    "cache": SAFE,
    "cache_size": SAFE,
    "context_size": SAFE,
    "resource_allocation": SAFE,
    # sensitive — owner-gated, proposal-only
    "autonomy_level": SENSITIVE,
    "confidence_threshold": SENSITIVE,
    "interrupt_threshold": SENSITIVE,
    "safety_model_selection": SENSITIVE,
    # forbidden — never tunable by Y
    "authority_ceiling": FORBIDDEN,
    "security_policy": FORBIDDEN,
    "identity_requirement": FORBIDDEN,
    "safety_threshold": FORBIDDEN,
    "human_override": FORBIDDEN,
    "audit_retention": FORBIDDEN,
}


def classify(param: str) -> str:
    """The tunability tier of a parameter. Unknown parameters are FORBIDDEN —
    the optimizer never touches anything it cannot prove is safe."""
    return _PARAM_TIER.get(param, FORBIDDEN)


def may_autotune(param: str) -> bool:
    """True only for SAFE parameters — the single tier Y may apply on its own."""
    return classify(param) == SAFE


@dataclass(frozen=True)
class Metric:
    """A measured performance value. ``lower_is_better`` captures direction so a
    caller can tell an improvement from a regression without hard-coding it."""

    name: str
    value: float
    unit: str = ""
    lower_is_better: bool = True

    def improves_on(self, other: "Metric") -> bool:
        if self.lower_is_better:
            return self.value < other.value
        return self.value > other.value


@dataclass(frozen=True)
class Bound:
    """An owner-set floor/ceiling for a tunable parameter."""

    param: str
    low: float
    high: float

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high

    def clamp(self, value: float) -> float:
        return max(self.low, min(self.high, value))


@dataclass(frozen=True)
class TuningProposal:
    """A proposed change to one parameter, with its tier and guard flags.

    Nothing here applies the change — the flags say what a caller *may* do:
    ``auto_applicable`` only when the parameter is SAFE and the proposed value is
    within bounds; ``proposal_only`` when SENSITIVE and within bounds (owner must
    approve); ``rejected`` when FORBIDDEN or out of bounds.
    """

    param: str
    current: float
    proposed: float
    tier: str
    within_bounds: bool
    metric_before: Optional[Metric] = None
    rationale: str = ""

    @property
    def rejected(self) -> bool:
        return self.tier == FORBIDDEN or not self.within_bounds

    @property
    def auto_applicable(self) -> bool:
        return self.tier == SAFE and self.within_bounds

    @property
    def proposal_only(self) -> bool:
        return self.tier == SENSITIVE and self.within_bounds

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rejected"] = self.rejected
        d["auto_applicable"] = self.auto_applicable
        d["proposal_only"] = self.proposal_only
        return d


def propose(param: str, current: float, proposed: float, *,
            bound: Optional[Bound] = None,
            metric_before: Optional[Metric] = None,
            rationale: str = "") -> TuningProposal:
    """Build a guarded tuning proposal. The tier is resolved from ``param`` (so a
    FORBIDDEN or unknown parameter is rejected regardless of the value), and
    ``within_bounds`` is checked against ``bound`` when one is given (no bound →
    treated as within bounds only for already-tunable tiers)."""
    tier = classify(param)
    if bound is not None:
        within = bound.contains(proposed)
    else:
        # No explicit bound: a SAFE/SENSITIVE param is considered in-bounds, but a
        # FORBIDDEN one stays rejected by its tier regardless.
        within = tier != FORBIDDEN
    return TuningProposal(
        param=param,
        current=current,
        proposed=proposed,
        tier=tier,
        within_bounds=within,
        metric_before=metric_before,
        rationale=rationale,
    )


@dataclass(frozen=True)
class OptimizationReport:
    proposals: Tuple[TuningProposal, ...]

    @property
    def auto_applicable(self) -> Tuple[TuningProposal, ...]:
        return tuple(p for p in self.proposals if p.auto_applicable)

    @property
    def for_owner(self) -> Tuple[TuningProposal, ...]:
        return tuple(p for p in self.proposals if p.proposal_only)

    @property
    def rejected(self) -> Tuple[TuningProposal, ...]:
        return tuple(p for p in self.proposals if p.rejected)

    def to_dict(self) -> dict:
        return {
            "proposals": [p.to_dict() for p in self.proposals],
            "auto_applicable": len(self.auto_applicable),
            "for_owner": len(self.for_owner),
            "rejected": len(self.rejected),
        }


def report(proposals: Iterable[TuningProposal]) -> OptimizationReport:
    """Roll a set of proposals up into auto-applicable / owner / rejected buckets."""
    return OptimizationReport(tuple(proposals))
