"""MCU certification — the scenario-class suite as a kernel primitive (Phase AE).

docs/KERNEL_PLAN.md, **Phase AE (MCU Certification)**. AE is the
systems-certification phase — *"does the complete JARVIS behave as one?"* — and
the **MCU System Done** bar. It is not a single gate but a suite of explicit
**scenario classes**, each meant to run closed-loop and journal-reconstructable:

    conversational          request → reasoning → action → verification
    proactive               observation → prediction → suggestion → response
    long_horizon            objective → days → restart → resume → completion
    delegation              JARVIS → child → result → synthesis
    failure                 action → HA failure → recovery
    security                ambiguous identity → deny / confirm
    conflicting_priorities  convenience vs. safety
    provider_failure        cloud unavailable → local degradation
    cognitive_error         bad belief → observation → correction
    restart                 mid-agency restart → reconciliation → resume

This module is the **pure record** of that suite: the canonical class list, a
:class:`ScenarioResult` per class (exercised? closed-loop? a note), and a
:class:`CertificationReport` with pure derivations — coverage (classes exercised
/ total), closed-loop rate (classes that closed / exercised), and ``is_certified``
(every class exercised AND closed). Pure: no Home Assistant import, no I/O,
deterministic. It lands pure and additive; the ladder from here is traces
(shadow) → a parity dashboard (coverage %, closed-loop %, invariants green) →
enforce (every class closes) + a certification CI gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

# ── the canonical scenario classes, in order ────────────────────────────────────
CONVERSATIONAL = "conversational"
PROACTIVE = "proactive"
LONG_HORIZON = "long_horizon"
DELEGATION = "delegation"
FAILURE = "failure"
SECURITY = "security"
CONFLICTING_PRIORITIES = "conflicting_priorities"
PROVIDER_FAILURE = "provider_failure"
COGNITIVE_ERROR = "cognitive_error"
RESTART = "restart"

CLASSES: Tuple[str, ...] = (
    CONVERSATIONAL, PROACTIVE, LONG_HORIZON, DELEGATION, FAILURE, SECURITY,
    CONFLICTING_PRIORITIES, PROVIDER_FAILURE, COGNITIVE_ERROR, RESTART,
)
_RANK: Dict[str, int] = {c: i for i, c in enumerate(CLASSES)}


@dataclass(frozen=True)
class ScenarioResult:
    """One scenario class's certification state: whether it has been exercised at
    all, whether that run CLOSED the loop (ran end-to-end, journal-reconstructable),
    and a short human note."""

    scenario: str
    exercised: bool = False
    closed_loop: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {"scenario": self.scenario, "exercised": self.exercised,
                "closed_loop": self.closed_loop, "note": self.note}


@dataclass(frozen=True)
class CertificationReport:
    """The suite's state at one instant — the MCU certification dashboard.

    ``results`` holds at most one :class:`ScenarioResult` per canonical class, in
    canonical order. A class absent from ``results`` is treated as not-exercised."""

    results: Tuple[ScenarioResult, ...] = ()

    def _by(self) -> Dict[str, ScenarioResult]:
        return {r.scenario: r for r in self.results if isinstance(r, ScenarioResult)}

    @property
    def exercised_classes(self) -> Tuple[str, ...]:
        by = self._by()
        return tuple(c for c in CLASSES if by.get(c) is not None and by[c].exercised)

    @property
    def closed_classes(self) -> Tuple[str, ...]:
        by = self._by()
        return tuple(c for c in CLASSES
                     if by.get(c) is not None and by[c].exercised and by[c].closed_loop)

    @property
    def missing_classes(self) -> Tuple[str, ...]:
        done = set(self.closed_classes)
        return tuple(c for c in CLASSES if c not in done)

    @property
    def coverage(self) -> float:
        """Fraction of canonical classes that have been exercised at all."""
        return len(self.exercised_classes) / len(CLASSES) if CLASSES else 0.0

    @property
    def closed_loop_rate(self) -> float:
        """Of the exercised classes, the fraction that closed the loop."""
        ex = self.exercised_classes
        return (len(self.closed_classes) / len(ex)) if ex else 0.0

    @property
    def is_certified(self) -> bool:
        """True only when EVERY canonical class has been exercised and closed."""
        return len(self.closed_classes) == len(CLASSES)

    def summary(self) -> str:
        """One-line, deterministic headline for the certification dashboard."""
        cov = f"{len(self.exercised_classes)}/{len(CLASSES)}"
        closed = f"{len(self.closed_classes)}/{len(CLASSES)}"
        state = "CERTIFIED" if self.is_certified else "incomplete"
        miss = ", ".join(self.missing_classes[:4])
        tail = f" — missing {miss}" if self.missing_classes else ""
        return (f"{state}: exercised {cov} ({self.coverage * 100:.0f}%), "
                f"closed {closed} ({self.closed_loop_rate * 100:.0f}% of exercised)"
                + tail)

    def to_dict(self) -> dict:
        by = self._by()
        return {
            "is_certified": self.is_certified,
            "coverage": round(self.coverage, 4),
            "closed_loop_rate": round(self.closed_loop_rate, 4),
            "exercised": list(self.exercised_classes),
            "closed": list(self.closed_classes),
            "missing": list(self.missing_classes),
            "results": [by[c].to_dict() for c in CLASSES if c in by],
        }


def result(scenario: str, *, exercised: bool = False, closed_loop: bool = False,
           note: str = "") -> ScenarioResult:
    """Build a :class:`ScenarioResult`, normalising the class name. A closed loop
    implies the class was exercised (you cannot close what you never ran)."""
    nm = str(scenario or "").strip().lower()
    closed = bool(closed_loop)
    return ScenarioResult(scenario=nm, exercised=bool(exercised) or closed,
                          closed_loop=closed, note=str(note or ""))


def report(results=None) -> CertificationReport:
    """Build a :class:`CertificationReport` from an iterable of
    :class:`ScenarioResult` (or ``(name, exercised, closed_loop, note)`` tuples).
    Order-independent; keeps canonical order, last-wins per class; drops anything
    outside the canonical class set. Pure and total."""
    out = []
    for r in (results or ()):
        if isinstance(r, ScenarioResult):
            out.append(r)
        elif isinstance(r, (tuple, list)) and r:
            # (name[, exercised[, closed_loop[, note]]]) — positional, padded.
            f = list(r) + [None] * (4 - len(r))
            out.append(result(f[0], exercised=bool(f[1]),
                              closed_loop=bool(f[2]), note=f[3] or ""))
    by = {r.scenario: r for r in out if r.scenario in _RANK}
    ordered = tuple(by[c] for c in CLASSES if c in by)
    return CertificationReport(results=ordered)


def from_map(mapping: Optional[Mapping[str, bool]] = None) -> CertificationReport:
    """Convenience: a report where every class LISTED in ``mapping`` was exercised,
    and its bool is whether that run closed the loop (so ``False`` is the real
    "exercised but open" state, not "absent"). Classes absent from ``mapping`` are
    not-exercised. Pure and total."""
    mapping = mapping or {}
    rows = [result(c, exercised=True, closed_loop=bool(mapping.get(c, False)))
            for c in CLASSES if c in mapping]
    return report(rows)


class CertificationLedger:
    """Accumulates scenario-class observations over a window into a rolling
    :class:`CertificationReport` — the certification dashboard's live tally.

    **Sticky-best per class**: once a class is observed closing the loop it STAYS
    closed for the window. Certification asks whether every class has been
    *demonstrated to close at least once*, not whether the latest run of it
    closed — so a later open pass of an already-closed class must not un-certify
    it. ``reset()`` starts a fresh window. Pure: no HA import, no I/O, no clock;
    non-canonical class names are ignored. Mirrors the shape of
    ``kernel.integration.LoopAccumulator`` (the Phase R parity tally)."""

    def __init__(self) -> None:
        self._best: Dict[str, ScenarioResult] = {}
        self._seen: int = 0

    def observe(self, scenario: str, *, closed_loop: bool = False,
                note: str = "") -> None:
        """Record one observation of a scenario class. Non-canonical names are
        ignored. Sticky-best: a class that has ever closed stays closed."""
        # observing a class at all means it was exercised; the bool is whether
        # that run closed the loop (an open observation is exercised-but-open).
        r = result(scenario, exercised=True, closed_loop=closed_loop, note=note)
        if r.scenario not in _RANK:
            return
        self._seen += 1
        prev = self._best.get(r.scenario)
        if prev is not None and prev.closed_loop and not r.closed_loop:
            # already demonstrated closed this window — keep it, refresh the note
            if note:
                self._best[r.scenario] = result(r.scenario, exercised=True,
                                                closed_loop=True, note=note)
            return
        self._best[r.scenario] = r

    @property
    def observations(self) -> int:
        """Total observations recorded this window (canonical classes only)."""
        return self._seen

    def report(self) -> CertificationReport:
        """The rolling certification report over everything observed so far."""
        return report(tuple(self._best.values()))

    def summary(self) -> str:
        """One-line headline: the report's summary, prefixed with the count."""
        if self._seen == 0:
            return "no observations recorded"
        return f"[{self._seen} obs] {self.report().summary()}"

    def to_dict(self) -> dict:
        d = self.report().to_dict()
        d["observations"] = self._seen
        return d

    def reset(self) -> None:
        self._best.clear()
        self._seen = 0
