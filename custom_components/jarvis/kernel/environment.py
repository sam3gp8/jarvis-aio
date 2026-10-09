"""Physical-world intelligence — state + objective functions (roadmap Phase X).

JARVIS already *senses* the physical world through scattered heuristics: `energy`
reads whole-home draw against a peak threshold with a never-shed critical-load
list; `sentinel` watches apertures (door/window/garage left open) on time
thresholds; `hazard_situation` watches a freeze threshold. Phase X gives those a
common, inspectable substrate: a read-only model of environmental **state** plus
**objective functions** that score comfort and efficiency, and — crucially — a
recommender that structurally obeys the safety seam and the priority ladder.

Pure: no Home Assistant import and no I/O. Readings are plain injected data, so
the model and its objectives are deterministic and unit-testable; the live
binders (energy / sentinel / climate) wire real sensors onto it at the shadow
rung. The one hard rule is encoded here, not left to callers:

    **comfort and efficiency are CONVENIENCE / HOUSEHOLD concerns and may never
    override an active safety concern.**

So the recommender *describes* — it emits advisory `Recommendation`s and flags
each as blocked whenever a safety concern outranks it (via `kernel.priority`). It
holds no actuator and grants nothing: "suggest, don't act", and even the
suggestion yields to safety.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, Optional, Tuple

from . import priority

# ── reading kinds ─────────────────────────────────────────────────────────────
TEMPERATURE = "temperature"
HUMIDITY = "humidity"
AIR_QUALITY = "air_quality"
POWER = "power"
APERTURE = "aperture"

# ── comfort classification bands ──────────────────────────────────────────────
BELOW = "below"
COMFORTABLE = "comfortable"
ABOVE = "above"

# ── objectives ────────────────────────────────────────────────────────────────
COMFORT = "comfort"
EFFICIENCY = "efficiency"

# Natural priority tier for each objective (both strictly below a safety concern).
_OBJECTIVE_TIER = {COMFORT: priority.CONVENIENCE, EFFICIENCY: priority.HOUSEHOLD}

_EPS = 1e-9


@dataclass(frozen=True)
class Reading:
    """One environmental measurement — plain injected data (no HA types)."""

    kind: str
    value: float
    area: Optional[str] = None
    entity_id: Optional[str] = None
    unit: str = ""


@dataclass(frozen=True)
class ComfortBand:
    """A comfort target for a measurement kind: a tolerated ``[low, high]`` range
    around an ``ideal`` (defaults to the midpoint)."""

    low: float
    high: float
    ideal: Optional[float] = None

    @property
    def target(self) -> float:
        return self.ideal if self.ideal is not None else (self.low + self.high) / 2.0

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high

    def classify(self, value: float) -> str:
        if value < self.low:
            return BELOW
        if value > self.high:
            return ABOVE
        return COMFORTABLE

    def score(self, value: float) -> float:
        """Comfort in ``[0, 1]``: 1.0 at the ideal, falling linearly to 0.0 one
        band-width away from it (≈0.5 at a band edge). Deterministic, clamped."""
        tolerance = max(self.high - self.low, _EPS)
        s = 1.0 - abs(value - self.target) / tolerance
        return max(0.0, min(1.0, s))


@dataclass(frozen=True)
class EnvironmentState:
    """A snapshot of environmental readings with small query helpers."""

    readings: Tuple[Reading, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.readings

    def by_kind(self, kind: str) -> Tuple[Reading, ...]:
        return tuple(r for r in self.readings if r.kind == kind)

    def by_area(self, area: Optional[str]) -> Tuple[Reading, ...]:
        return tuple(r for r in self.readings if r.area == area)

    def latest(self, kind: str, area: Optional[str] = None) -> Optional[Reading]:
        """The last-provided reading of ``kind`` (optionally within ``area``)."""
        match = [r for r in self.readings
                 if r.kind == kind and (area is None or r.area == area)]
        return match[-1] if match else None


# ── objective functions ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class ComfortEntry:
    reading: Reading
    classification: str
    score: float


@dataclass(frozen=True)
class ComfortReport:
    entries: Tuple[ComfortEntry, ...]
    overall: float                      # mean score over readings with a band
    worst: Optional[ComfortEntry]       # lowest-scoring entry (None if no entries)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def comfort(state: EnvironmentState,
            bands: Dict[str, ComfortBand]) -> ComfortReport:
    """Score every reading whose kind has a `ComfortBand`. Pure aggregation."""
    entries = []
    for r in state.readings:
        band = bands.get(r.kind)
        if band is None:
            continue
        entries.append(ComfortEntry(r, band.classify(r.value), band.score(r.value)))
    if entries:
        overall = sum(e.score for e in entries) / len(entries)
        worst = min(entries, key=lambda e: e.score)
    else:
        overall, worst = 1.0, None   # nothing to judge → nothing uncomfortable
    return ComfortReport(tuple(entries), overall, worst)


@dataclass(frozen=True)
class EfficiencyReport:
    draw: float
    peak: float
    over: bool
    headroom: float          # peak − draw (negative when over)
    utilization: float       # draw / peak, clamped to [0, …]

    def to_dict(self) -> dict:
        return asdict(self)


def efficiency(power_w: float, *, peak_w: float) -> EfficiencyReport:
    """Assess whole-home draw against a peak threshold. Pure."""
    peak = max(peak_w, _EPS)
    headroom = peak_w - power_w
    return EfficiencyReport(
        draw=power_w,
        peak=peak_w,
        over=power_w > peak_w,
        headroom=headroom,
        utilization=max(0.0, power_w / peak),
    )


# ── recommender (advisory only; yields to safety) ─────────────────────────────

@dataclass(frozen=True)
class Recommendation:
    """An advisory suggestion from the model — never an action.

    ``tier`` is its place on the priority ladder; ``blocked_by_safety`` is set when
    an active safety concern outranks it, so the suggestion is surfaced but marked
    not-to-be-pursued. ``actionable`` is the single flag a caller reads: it is
    never True while a safety concern is active.
    """

    objective: str
    subject: str
    detail: str
    tier: str
    blocked_by_safety: bool = False

    @property
    def actionable(self) -> bool:
        return not self.blocked_by_safety

    def to_dict(self) -> dict:
        d = asdict(self)
        d["actionable"] = self.actionable
        return d


def _guard(tier: str, *, safety_active: bool, safety_tier: str) -> bool:
    """True if a concern at ``tier`` is blocked by an active ``safety_tier``
    concern — i.e. it may NOT override it (`kernel.priority.may_override`)."""
    if not safety_active:
        return False
    return not priority.may_override(tier, safety_tier)


def recommend(comfort_report: Optional[ComfortReport] = None,
              efficiency_report: Optional[EfficiencyReport] = None,
              *,
              safety_active: bool = False,
              safety_tier: str = priority.LIFE_SAFETY) -> Tuple[Recommendation, ...]:
    """Build advisory recommendations from the objective reports.

    A comfort recommendation is emitted for each reading classified outside its
    band; an efficiency recommendation when draw is over peak. Every one is tagged
    with its priority tier and flagged ``blocked_by_safety`` when a safety concern
    outranks it — so comfort/efficiency can never override safety, structurally.
    """
    recs = []
    if comfort_report is not None:
        for e in comfort_report.entries:
            if e.classification == COMFORTABLE:
                continue
            subject = e.reading.area or e.reading.entity_id or e.reading.kind
            recs.append(Recommendation(
                objective=COMFORT,
                subject=subject,
                detail=(f"{e.reading.kind} {e.classification} comfort band "
                        f"(score {e.score:.2f})"),
                tier=_OBJECTIVE_TIER[COMFORT],
                blocked_by_safety=_guard(_OBJECTIVE_TIER[COMFORT],
                                         safety_active=safety_active,
                                         safety_tier=safety_tier),
            ))
    if efficiency_report is not None and efficiency_report.over:
        recs.append(Recommendation(
            objective=EFFICIENCY,
            subject="whole_home",
            detail=(f"draw {efficiency_report.draw:.0f}W over peak "
                    f"{efficiency_report.peak:.0f}W"),
            tier=_OBJECTIVE_TIER[EFFICIENCY],
            blocked_by_safety=_guard(_OBJECTIVE_TIER[EFFICIENCY],
                                     safety_active=safety_active,
                                     safety_tier=safety_tier),
        ))
    return tuple(recs)


@dataclass(frozen=True)
class EnvironmentAssessment:
    comfort: ComfortReport
    efficiency: Optional[EfficiencyReport]
    recommendations: Tuple[Recommendation, ...]

    @property
    def actionable(self) -> Tuple[Recommendation, ...]:
        return tuple(r for r in self.recommendations if r.actionable)

    def to_dict(self) -> dict:
        return {
            "comfort": self.comfort.to_dict(),
            "efficiency": self.efficiency.to_dict() if self.efficiency else None,
            "recommendations": [r.to_dict() for r in self.recommendations],
        }


def assess(state: EnvironmentState,
           *,
           bands: Dict[str, ComfortBand],
           power_w: Optional[float] = None,
           peak_w: Optional[float] = None,
           safety_active: bool = False,
           safety_tier: str = priority.LIFE_SAFETY) -> EnvironmentAssessment:
    """One-shot: score comfort + efficiency and build the guarded recommendations.

    ``power_w`` / ``peak_w`` together add the efficiency objective; either missing
    skips it. Nothing here acts — the result is advisory, and every recommendation
    yields to an active safety concern.
    """
    comfort_report = comfort(state, bands)
    eff = (efficiency(power_w, peak_w=peak_w)
           if power_w is not None and peak_w is not None else None)
    recs = recommend(comfort_report, eff,
                     safety_active=safety_active, safety_tier=safety_tier)
    return EnvironmentAssessment(comfort_report, eff, recs)
