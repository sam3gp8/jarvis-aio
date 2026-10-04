"""Probabilistic beliefs (kernel Phase 6, docs/KERNEL_PLAN.md).

A belief is a proposition JARVIS holds with a probability, backed by accumulated
**evidence** (each with a source and weight), subject to **time decay** toward
uncertainty, and able to register **contradiction** when evidence points both
ways. This generalises the flat "confidence" numbers scattered across the
knowledge store into one update rule.

Pure: no Home Assistant import, no I/O — a `Belief` is an immutable value and
every update returns a new one, so it is trivially testable and seedable from
knowledge-store confidences. Phase 6 ships it additively; callers adopt it later.

Update model: log-odds (logit) pooling. Each piece of evidence shifts the
log-odds by ``weight`` toward its direction (support → +, refute → −); decay
pulls the log-odds back toward 0 (p=0.5) over time. This keeps probabilities in
(0,1), makes independent evidence combine sensibly, and makes contradiction
visible as support and refutation both being present.
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

_EPS = 1e-6


def _clamp_p(p: float) -> float:
    return min(1.0 - _EPS, max(_EPS, p))


def _logit(p: float) -> float:
    p = _clamp_p(p)
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


@dataclass(frozen=True)
class Evidence:
    """One observation bearing on a proposition.

    ``supports`` True = evidence for the proposition, False = against.
    ``weight`` is its strength in log-odds (≈ how many "nats" it shifts belief).
    """

    source: str
    supports: bool
    weight: float = 1.0
    ts: float = field(default_factory=time.time)
    note: str = ""


@dataclass(frozen=True)
class Belief:
    """A proposition held with a probability and its supporting evidence."""

    proposition: str
    probability: float = 0.5
    evidence: Tuple[Evidence, ...] = ()
    updated_ts: float = field(default_factory=time.time)
    # Log-odds per day pulled back toward 0 (p→0.5). 0 disables decay.
    decay_per_day: float = 0.0

    # ── updates (frozen-safe) ──────────────────────────────────────────────────
    def with_evidence(self, ev: Evidence) -> "Belief":
        """Return a copy updated by one piece of evidence (log-odds pooling)."""
        base = _logit(self.probability)
        shift = abs(ev.weight) * (1.0 if ev.supports else -1.0)
        return replace(
            self,
            probability=_sigmoid(base + shift),
            evidence=self.evidence + (ev,),
            updated_ts=ev.ts,
        )

    def decayed(self, *, now: Optional[float] = None) -> "Belief":
        """Return a copy with time-decay applied toward p=0.5 (uncertainty)."""
        if self.decay_per_day <= 0:
            return self
        now = time.time() if now is None else now
        days = max(0.0, (now - self.updated_ts) / 86400.0)
        if days == 0:
            return self
        lo = _logit(self.probability)
        pull = self.decay_per_day * days
        if lo > 0:
            lo = max(0.0, lo - pull)
        else:
            lo = min(0.0, lo + pull)
        return replace(self, probability=_sigmoid(lo), updated_ts=now)

    # ── introspection ──────────────────────────────────────────────────────────
    @property
    def supported(self) -> bool:
        return self.probability >= 0.5

    @property
    def contradicted(self) -> bool:
        """True when there is meaningful evidence on BOTH sides."""
        has_for = any(e.supports for e in self.evidence)
        has_against = any(not e.supports for e in self.evidence)
        return has_for and has_against

    def contradiction_strength(self) -> float:
        """0..1: how balanced the opposing evidence is (1 = perfectly conflicted)."""
        f = sum(abs(e.weight) for e in self.evidence if e.supports)
        a = sum(abs(e.weight) for e in self.evidence if not e.supports)
        total = f + a
        if total <= 0 or f <= 0 or a <= 0:
            return 0.0
        return 1.0 - abs(f - a) / total

    def to_dict(self) -> dict:
        d = asdict(self)
        d["evidence"] = [asdict(e) for e in self.evidence]
        return d


def seed_from_confidence(proposition: str, confidence: float, *, source: str = "knowledge",
                         decay_per_day: float = 0.0) -> Belief:
    """Seed a belief from a flat knowledge-store confidence (0..1)."""
    return Belief(proposition=proposition, probability=_clamp_p(float(confidence)),
                  decay_per_day=decay_per_day,
                  evidence=(Evidence(source=source, supports=confidence >= 0.5,
                                     weight=abs(_logit(_clamp_p(float(confidence)))),
                                     note="seed"),))


# ── identity (MCU Phase E/E1, north-star seed) ──────────────────────────────────
# A single, stable self-assertion JARVIS holds about *who it is*. Kept deliberately
# minimal: one high-confidence belief, pure and additive. It is exposed in the
# belief *view* (WorldModel.beliefs) but is NOT surfaced into the live LLM prompt —
# doing that changes what JARVIS is told about itself, a user-visible self-model
# step that is proposed to the owner rather than enabled here.
def identity_assertion(*, name: str = "JARVIS",
                       role: str = "the household's home assistant") -> Belief:
    """The minimal 'who JARVIS is' self-belief (probability ~0.99)."""
    return seed_from_confidence(f"{name} is {role}", 0.99, source="identity")
