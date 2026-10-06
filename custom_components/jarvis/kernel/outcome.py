"""Canonical outcome model (Epistemic Fabric — audit-added, pre-Phase M).

The 2026-10 audit flagged that "learning" (Phase M) is unconstrained unless an
action produces a *structured* outcome rather than a log line the LLM re-reads.
This primitive is that structure: a canonical :class:`Outcome` sitting between
verification and learning, capturing what was intended, what was observed,
whether it succeeded, how it deviated, the attributed cause, side effects, any
user/environmental feedback, and — distilled from all of that — a single bounded
``learning_signal`` that Phase M consumes.

It lands **pure**: the record, a derivation from a verification result, a
deterministic ``learning_signal`` in ``[-1, 1]``, and simple roll-ups. Nothing
live produces outcomes yet — the actuation/verify path records one in shadow,
parity against current feedback, enforce when M reads them. No Home Assistant
import, so it is deterministic and unit-testable, and nothing here raises into
an actuation path.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple


def _clamp(x, lo: float, hi: float, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def learning_signal_for(success: bool, confidence: float) -> float:
    """Distill a bounded reinforcement signal in ``[-1, 1]``.

    A succeeded outcome yields ``+confidence``; a failed one ``-confidence``. So
    a confident success reinforces strongly, a confident failure penalizes
    strongly, and a low-confidence result (either way) barely moves anything —
    exactly what a clamped, auditable learner should see. Pure and total.
    """
    c = _clamp(confidence, 0.0, 1.0)
    return c if success else -c


@dataclass(frozen=True)
class Outcome:
    """The structured result of one consequential action.

    ``success`` is the verdict; ``confidence`` how sure we are of it;
    ``deviation`` how the observed result differed from the intended one;
    ``cause`` the attributed reason for a failure/deviation; ``learning_signal``
    the distilled scalar (``[-1, 1]``) Phase M consumes. ``actor``/``capability``/
    ``correlation_id`` tie the outcome back into the agency chain.
    """

    ts: float = 0.0
    intended_result: str = ""
    observed_result: str = ""
    success: bool = False
    confidence: float = 0.0
    deviation: str = ""
    cause: str = ""
    side_effects: Tuple[str, ...] = ()
    user_feedback: str = ""
    environmental_feedback: str = ""
    learning_signal: float = 0.0
    actor: str = ""
    capability: str = ""
    correlation_id: str = ""

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "intended_result": self.intended_result,
            "observed_result": self.observed_result,
            "success": self.success,
            "confidence": self.confidence,
            "deviation": self.deviation,
            "cause": self.cause,
            "side_effects": list(self.side_effects),
            "user_feedback": self.user_feedback,
            "environmental_feedback": self.environmental_feedback,
            "learning_signal": self.learning_signal,
            "actor": self.actor,
            "capability": self.capability,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, d: Mapping) -> "Outcome":
        return cls(
            ts=_clamp(d.get("ts", 0.0), 0.0, float("inf")),
            intended_result=str(d.get("intended_result", "") or ""),
            observed_result=str(d.get("observed_result", "") or ""),
            success=bool(d.get("success", False)),
            confidence=_clamp(d.get("confidence", 0.0), 0.0, 1.0),
            deviation=str(d.get("deviation", "") or ""),
            cause=str(d.get("cause", "") or ""),
            side_effects=tuple(str(s) for s in (d.get("side_effects") or ())),
            user_feedback=str(d.get("user_feedback", "") or ""),
            environmental_feedback=str(d.get("environmental_feedback", "") or ""),
            learning_signal=_clamp(d.get("learning_signal", 0.0), -1.0, 1.0),
            actor=str(d.get("actor", "") or ""),
            capability=str(d.get("capability", "") or ""),
            correlation_id=str(d.get("correlation_id", "") or ""),
        )


def record_outcome(
    *,
    intended_result: str = "",
    observed_result: str = "",
    success: bool = False,
    confidence: float = 0.0,
    deviation: str = "",
    cause: str = "",
    side_effects: Iterable[str] = (),
    user_feedback: str = "",
    environmental_feedback: str = "",
    actor: str = "",
    capability: str = "",
    correlation_id: str = "",
    learning_signal: Optional[float] = None,
    now: Optional[Callable[[], float]] = None,
) -> Outcome:
    """Build an :class:`Outcome`, distilling ``learning_signal`` if not supplied.

    When ``learning_signal`` is omitted it is derived via
    :func:`learning_signal_for` from ``success`` and ``confidence``. Pure: the
    caller supplies the already-extracted intent/observation/verdict.
    """
    clock = now or time.time
    sig = (
        _clamp(learning_signal, -1.0, 1.0)
        if learning_signal is not None
        else learning_signal_for(success, confidence)
    )
    return Outcome(
        ts=float(clock()),
        intended_result=str(intended_result or ""),
        observed_result=str(observed_result or ""),
        success=bool(success),
        confidence=_clamp(confidence, 0.0, 1.0),
        deviation=str(deviation or ""),
        cause=str(cause or ""),
        side_effects=tuple(str(s) for s in side_effects),
        user_feedback=str(user_feedback or ""),
        environmental_feedback=str(environmental_feedback or ""),
        learning_signal=sig,
        actor=str(actor or ""),
        capability=str(capability or ""),
        correlation_id=str(correlation_id or ""),
    )


def from_verification(
    *,
    intended_result: str,
    observed_result: str,
    verified: bool,
    confidence: float = 1.0,
    actor: str = "",
    capability: str = "",
    correlation_id: str = "",
    side_effects: Iterable[str] = (),
    now: Optional[Callable[[], float]] = None,
) -> Outcome:
    """Derive an :class:`Outcome` from a postcondition-verification result.

    ``verified`` is the success verdict. When it is False, the gap between
    ``intended_result`` and ``observed_result`` is recorded as the ``deviation``
    automatically, so a caller that only has the two strings still produces a
    useful outcome. ``learning_signal`` is distilled from the verdict.
    """
    deviation = ""
    if not verified:
        deviation = f"intended {intended_result!r}, observed {observed_result!r}"
    return record_outcome(
        intended_result=intended_result,
        observed_result=observed_result,
        success=bool(verified),
        confidence=confidence,
        deviation=deviation,
        side_effects=side_effects,
        actor=actor,
        capability=capability,
        correlation_id=correlation_id,
        now=now,
    )


@dataclass(frozen=True)
class OutcomeStats:
    """Rolled-up outcome statistics over a set."""

    count: int = 0
    successes: int = 0
    mean_confidence: float = 0.0
    mean_learning_signal: float = 0.0

    @property
    def success_rate(self) -> float:
        return (self.successes / self.count) if self.count else 0.0

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "successes": self.successes,
            "success_rate": round(self.success_rate, 6),
            "mean_confidence": round(self.mean_confidence, 6),
            "mean_learning_signal": round(self.mean_learning_signal, 6),
        }


def summarize(outcomes: Iterable[Outcome]) -> OutcomeStats:
    """Count, successes, and mean confidence / learning signal. Pure and total."""
    count = succ = 0
    conf_sum = sig_sum = 0.0
    for o in outcomes:
        if not isinstance(o, Outcome):
            continue
        count += 1
        if o.success:
            succ += 1
        conf_sum += o.confidence
        sig_sum += o.learning_signal
    if not count:
        return OutcomeStats()
    return OutcomeStats(
        count=count,
        successes=succ,
        mean_confidence=conf_sum / count,
        mean_learning_signal=sig_sum / count,
    )


def by_capability(outcomes: Iterable[Outcome]) -> Dict[str, OutcomeStats]:
    """Outcome stats grouped by capability — the per-capability track record
    Phase N autonomy and Phase M learning both read."""
    buckets: Dict[str, List[Outcome]] = {}
    for o in outcomes:
        if not isinstance(o, Outcome):
            continue
        buckets.setdefault(o.capability or "", []).append(o)
    return {k: summarize(v) for k, v in buckets.items()}
