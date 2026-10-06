"""Conflict resolution — adjudicate contradictory evidence (Epistemic Fabric).

Once JARVIS has beliefs, sensors, predictions and people, contradictions are
inevitable: the camera says the garage is empty, the phone says the user is in
it, a motion sensor just fired. The 2026-10 audit called for a **formal** rule
for which source wins — evidence ranking by source reliability, recency,
confidence and corroboration — rather than "whichever subsystem ran last." This
primitive is that rule. It **must exist before advanced world-model reasoning**
so contradictions resolve by policy.

Inputs are :class:`kernel.provenance.Provenance` records (value + source +
confidence + observed-at + corroboration + expiry). :func:`resolve` scores each
fresh candidate (``confidence × source-reliability × recency × corroboration``),
sums scores per distinct value (independent agreeing sources reinforce), and
returns the winner — flagged **contested** when the runner-up value is within a
margin, so an unclear conflict can defer rather than pick blindly.

Pure: no Home Assistant import, deterministic, never raises. Nothing live routes
decisions through it yet (world-model / situation consult it in shadow, parity,
then enforce).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .provenance import Provenance

# Reliability assumed for a source not in the caller's map. Mid-scale: an
# unknown source neither dominates nor is ignored.
DEFAULT_RELIABILITY = 0.5

# Recency half-life (seconds): a candidate's weight halves every half-life of
# age. 0 or None disables recency weighting.
DEFAULT_HALF_LIFE = 300.0

# When the runner-up value's total score is within this fraction of the top
# value's, the result is contested and `resolved` is False.
DEFAULT_CONTEST_MARGIN = 0.15


def _clamp01(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def reliability_of(source: str, reliabilities: Optional[Mapping[str, float]],
                   default: float = DEFAULT_RELIABILITY) -> float:
    if not reliabilities:
        return _clamp01(default)
    return _clamp01(reliabilities.get(source, default))


def _recency_factor(age: float, half_life: Optional[float]) -> float:
    if not half_life or half_life <= 0:
        return 1.0
    if age <= 0:
        return 1.0
    return 0.5 ** (age / float(half_life))


def score_candidate(
    p: Provenance,
    *,
    reliabilities: Optional[Mapping[str, float]] = None,
    now: Optional[float] = None,
    half_life: Optional[float] = DEFAULT_HALF_LIFE,
) -> float:
    """Evidence weight of one candidate: confidence × reliability × recency ×
    corroboration bonus. A corroborated source gets up to +50%. Pure and total."""
    clock = now if now is not None else time.time()
    rel = reliability_of(p.source, reliabilities)
    recency = _recency_factor(p.age(clock), half_life)
    corrob = 1.0 + min(len(p.corroboration) * 0.1, 0.5)
    return _clamp01(p.confidence) * rel * recency * corrob


@dataclass(frozen=True)
class Resolution:
    """The outcome of adjudicating contradictory candidates."""

    value: Any = None
    winner: Optional[Provenance] = None
    score: float = 0.0
    resolved: bool = False
    contested: bool = False
    runner_up_value: Any = None
    ranked: Tuple[Tuple[Any, float], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "winner": self.winner.to_dict() if self.winner else None,
            "score": round(self.score, 6),
            "resolved": self.resolved,
            "contested": self.contested,
            "runner_up_value": self.runner_up_value,
            "ranked": [[v, round(s, 6)] for v, s in self.ranked],
        }


def resolve(
    candidates: Iterable[Provenance],
    *,
    reliabilities: Optional[Mapping[str, float]] = None,
    now: Optional[float] = None,
    half_life: Optional[float] = DEFAULT_HALF_LIFE,
    contest_margin: float = DEFAULT_CONTEST_MARGIN,
) -> Resolution:
    """Adjudicate contradictory candidates into one winning value.

    Only *fresh* candidates count. Each is scored, scores are summed per distinct
    value (so independent agreeing sources reinforce), and the highest-scoring
    value wins — carrying the single strongest provenance for that value as
    ``winner``. ``resolved`` is True only when a clear winner clears the
    runner-up by ``contest_margin``; otherwise ``contested`` is True and the
    caller should defer (seek confirmation) rather than pick blindly. Pure and
    total — an empty or all-stale input yields an unresolved, uncontested result.
    """
    clock = now if now is not None else time.time()
    fresh = [p for p in candidates if isinstance(p, Provenance) and p.is_fresh(clock)]
    if not fresh:
        return Resolution()

    by_value_score: Dict[Any, float] = {}
    by_value_best: Dict[Any, Tuple[float, Provenance]] = {}
    # Values may be unhashable in theory; key on repr for grouping, keep original.
    orig: Dict[Any, Any] = {}
    for p in fresh:
        key = repr(p.value)
        orig.setdefault(key, p.value)
        s = score_candidate(p, reliabilities=reliabilities, now=clock, half_life=half_life)
        by_value_score[key] = by_value_score.get(key, 0.0) + s
        best = by_value_best.get(key)
        if best is None or s > best[0]:
            by_value_best[key] = (s, p)

    ranked_keys = sorted(by_value_score.items(), key=lambda kv: kv[1], reverse=True)
    top_key, top_score = ranked_keys[0]
    runner_key, runner_score = (ranked_keys[1] if len(ranked_keys) > 1 else (None, 0.0))

    contested = (
        runner_key is not None
        and top_score > 0
        and (top_score - runner_score) < _clamp01(contest_margin) * top_score
    )
    resolved = top_score > 0 and not contested

    ranked = tuple((orig[k], sc) for k, sc in ranked_keys)
    return Resolution(
        value=orig[top_key],
        winner=by_value_best[top_key][1],
        score=top_score,
        resolved=resolved,
        contested=contested,
        runner_up_value=(orig[runner_key] if runner_key is not None else None),
        ranked=ranked,
    )
