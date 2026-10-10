"""Research & discovery — investigative agency (roadmap Phase AD) — pure primitive.

Bounded, cited, tool-using inquiry (diagnose an anomaly, research an answer). The
audit's sharpening is **evidence provenance, not merely citations**: MCU JARVIS
must distinguish *fact vs observation vs inference vs hypothesis vs prediction vs
recommendation*, and every result preserves source, timestamp, confidence, claim
and evidence — and surfaces contradictions — or a "research agent" is just
another answer generator. A hard **spend cap** bounds every investigation.

Pure: no Home Assistant import, no I/O, no clock, no network — findings (with
their injected timestamps) and the budget are passed in, and
investigate/synthesize are total, deterministic derivations. A live binder (the
tool-using gather loop with a real budget + clock) wires onto it at the shadow
rung; nothing live consumes it yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List, Optional, Tuple

# Epistemic claim types, weakest → strongest grounding is NOT a total order, but
# this ranking is what synthesis uses to prefer better-grounded claims.
FACT = "fact"                   # established, high-trust
OBSERVATION = "observation"     # directly sensed/measured
INFERENCE = "inference"         # derived from facts/observations
PREDICTION = "prediction"       # about the future
HYPOTHESIS = "hypothesis"       # proposed, unverified
RECOMMENDATION = "recommendation"  # a suggested action
CLAIM_TYPES = (FACT, OBSERVATION, INFERENCE, PREDICTION, HYPOTHESIS, RECOMMENDATION)

# Grounding rank (higher = better grounded) — ties broken by confidence.
_GROUND = {FACT: 5, OBSERVATION: 4, INFERENCE: 3, PREDICTION: 2, HYPOTHESIS: 1,
           RECOMMENDATION: 0}


def _norm_type(value) -> str:
    v = str(value or "").strip().lower()
    return v if v in CLAIM_TYPES else HYPOTHESIS   # fail toward the weakest


def _clamp01(x) -> float:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f < 0.0 else 1.0 if f > 1.0 else f


def _nonneg(x) -> float:
    try:
        f = float(x)
        return f if f >= 0.0 else 0.0
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class Finding:
    """One piece of evidence for an inquiry, with its provenance. A claim is
    *typed* (fact/observation/inference/…), sourced, timestamped (valid-as-of,
    injected — the primitive holds no clock) and confidence-scored. Total."""

    claim: str
    claim_type: str = HYPOTHESIS
    source: str = ""
    confidence: float = 0.5
    valid_as_of: float = 0.0        # caller-supplied timestamp; 0 = unknown
    evidence: Tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "claim", str(self.claim or "").strip())
        object.__setattr__(self, "claim_type", _norm_type(self.claim_type))
        object.__setattr__(self, "source", str(self.source or "").strip())
        object.__setattr__(self, "confidence", _clamp01(self.confidence))
        object.__setattr__(self, "valid_as_of", _nonneg(self.valid_as_of))
        ev = self.evidence or ()
        object.__setattr__(self, "evidence",
                           tuple(str(e).strip() for e in ev if str(e).strip()))

    @property
    def grounding(self) -> int:
        return _GROUND.get(self.claim_type, 0)

    def to_dict(self) -> dict:
        return {"claim": self.claim, "claim_type": self.claim_type,
                "source": self.source, "confidence": self.confidence,
                "valid_as_of": self.valid_as_of, "evidence": list(self.evidence)}


@dataclass(frozen=True)
class Inquiry:
    """A bounded investigation: a question, the findings gathered so far, and a
    spend cap (``budget`` units) with how much has been ``spent``. Immutable —
    :func:`record` returns a new Inquiry. Total."""

    question: str
    budget: float = 0.0
    spent: float = 0.0
    findings: Tuple[Finding, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "question", str(self.question or "").strip())
        object.__setattr__(self, "budget", _nonneg(self.budget))
        object.__setattr__(self, "spent", _nonneg(self.spent))
        object.__setattr__(self, "findings",
                           tuple(f for f in (self.findings or ()) if isinstance(f, Finding)))

    @property
    def remaining(self) -> float:
        return max(0.0, self.budget - self.spent)

    @property
    def within_budget(self) -> bool:
        return self.spent <= self.budget

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0.0

    def to_dict(self) -> dict:
        return {"question": self.question, "budget": self.budget,
                "spent": self.spent, "remaining": self.remaining,
                "findings": [f.to_dict() for f in self.findings]}


def start(question: str, *, budget: float = 0.0) -> Inquiry:
    """Open a budget-capped inquiry. Pure constructor."""
    return Inquiry(question=question, budget=budget)


def record(inquiry: Inquiry, finding: Finding, *, cost: float = 0.0) -> Inquiry:
    """Add a finding, charging ``cost`` against the budget. **The spend cap is
    hard**: a finding whose ``cost`` would overrun the budget is REFUSED and the
    inquiry returns unchanged — a bounded investigation can never silently
    overspend. A zero-cost finding is always allowed. Total; never raises."""
    if not isinstance(inquiry, Inquiry) or not isinstance(finding, Finding) or not finding.claim:
        return inquiry if isinstance(inquiry, Inquiry) else start("")
    c = _nonneg(cost)
    # Hard cap: refuse only when the charge would OVERRUN the budget. A zero-cost
    # finding is always allowed (it spends nothing); any positive cost that would
    # exceed the budget is refused, so a bounded investigation can never overspend.
    if (inquiry.spent + c) > inquiry.budget:
        return inquiry
    return replace(inquiry, spent=inquiry.spent + c,
                   findings=inquiry.findings + (finding,))


def contradictions(findings) -> List[Tuple[Finding, Finding]]:
    """Pairs of findings that assert opposing claims about the same subject — a
    naive but deterministic signal: same source-or-subject prefix, one negating
    the other (``not``/``no`` token differs). Kept simple and total; the live
    layer can supply a richer contradiction detector. Never raises."""
    fs = [f for f in (findings or []) if isinstance(f, Finding) and f.claim]
    out: List[Tuple[Finding, Finding]] = []

    def _neg(s: str) -> bool:
        toks = s.lower().split()
        return "not" in toks or "no" in toks or s.lower().startswith("no ")

    for i in range(len(fs)):
        for j in range(i + 1, len(fs)):
            a, b = fs[i], fs[j]
            sa = a.claim.lower().replace("not ", "").replace("no ", "")
            sb = b.claim.lower().replace("not ", "").replace("no ", "")
            if sa == sb and _neg(a.claim) != _neg(b.claim):
                out.append((a, b))
    return out


@dataclass(frozen=True)
class Conclusion:
    """The synthesized answer: the best-grounded claim, its support, the overall
    confidence, and any contradictions surfaced (never hidden)."""

    answer: str
    claim_type: str
    confidence: float
    supported_by: int
    contradicted: bool
    within_budget: bool

    def to_dict(self) -> dict:
        return {"answer": self.answer, "claim_type": self.claim_type,
                "confidence": self.confidence, "supported_by": self.supported_by,
                "contradicted": self.contradicted, "within_budget": self.within_budget}


def synthesize(inquiry: Inquiry) -> Optional[Conclusion]:
    """Derive a conclusion from the gathered findings: pick the best-grounded,
    highest-confidence claim as the answer, count how many findings support it,
    and flag whether it is contradicted. Returns ``None`` when there is nothing to
    conclude. Confidence is **capped** when a contradiction exists, so an
    investigation never reports false certainty. Total; never raises."""
    if not isinstance(inquiry, Inquiry) or not inquiry.findings:
        return None
    best = sorted(inquiry.findings,
                  key=lambda f: (f.grounding, f.confidence), reverse=True)[0]
    supported = sum(1 for f in inquiry.findings
                    if f.claim.lower() == best.claim.lower())
    contra = bool(contradictions(inquiry.findings))
    conf = best.confidence
    if contra:
        conf = min(conf, 0.5)      # unresolved contradiction caps certainty
    return Conclusion(
        answer=best.claim,
        claim_type=best.claim_type,
        confidence=round(conf, 4),
        supported_by=supported,
        contradicted=contra,
        within_budget=inquiry.within_budget,
    )
