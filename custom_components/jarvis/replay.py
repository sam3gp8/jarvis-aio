"""Offline replay / policy evaluation over recorded Decision Records (v7.48.0).

Re-applies a candidate decision policy to the decisions JARVIS already made and,
for the ones with a known outcome, reports how the calls would change and whether
accuracy improves — so a threshold change can be evaluated against real history
before it ships.

The policy modelled here is the general one every tier shares: *act only when
confidence ≥ threshold*. Each recorded decision carries a confidence and, once
judged, an outcome of ``"right"`` or ``"wrong"``. Sweeping the threshold sorts
each judged decision into one of four buckets:

    acted (conf ≥ T) & right      → kept a good call        (correct)
    acted (conf ≥ T) & wrong      → still made a mistake     (incorrect)
    held  (conf < T) & wrong      → avoided a mistake        (correct)
    held  (conf < T) & right      → suppressed a good call   (incorrect)

Maximising (kept-right + avoided-mistake) picks the threshold that best separates
right decisions from wrong ones by confidence. Everything here is pure over a list
of record dicts — nothing mutates records, calls a model, or acts on the home.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Verdict vocabulary used across the integration (see decision_record.set_outcome).
RIGHT = "right"
WRONG = "wrong"

# Below this many judged samples a recommendation is withheld — a threshold tuned
# on a handful of outcomes would chase noise. Cold-start gate, same spirit as the
# pattern/routine learning that stays quiet until it has enough evidence.
DEFAULT_MIN_SAMPLES = 25


@dataclass
class ReplayResult:
    """Outcome of replaying one confidence threshold over judged records."""
    threshold: float
    kept_right: int = 0        # acted (conf ≥ T) and it was right
    kept_wrong: int = 0        # acted and it was wrong
    suppressed_right: int = 0  # held (conf < T) but it was right — a good call lost
    suppressed_wrong: int = 0  # held and it was wrong — a mistake avoided

    @property
    def total(self) -> int:
        return self.kept_right + self.kept_wrong + self.suppressed_right + self.suppressed_wrong

    @property
    def correct(self) -> int:
        """Decisions the policy got right: kept a right call or avoided a wrong one."""
        return self.kept_right + self.suppressed_wrong

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def mistakes_avoided(self) -> int:
        return self.suppressed_wrong

    @property
    def good_calls_lost(self) -> int:
        return self.suppressed_right

    @property
    def acted(self) -> int:
        return self.kept_right + self.kept_wrong

    def to_dict(self) -> dict:
        return {
            "threshold": round(self.threshold, 3),
            "samples": self.total,
            "accuracy": round(self.accuracy, 3),
            "kept_right": self.kept_right,
            "kept_wrong": self.kept_wrong,
            "suppressed_right": self.suppressed_right,
            "suppressed_wrong": self.suppressed_wrong,
            "mistakes_avoided": self.mistakes_avoided,
            "good_calls_lost": self.good_calls_lost,
        }


def _judged(records) -> list:
    """(confidence, is_right) for records with a numeric confidence AND a
    right/wrong outcome. Everything else is skipped — unjudged decisions and
    ones logged without a confidence carry no signal for threshold evaluation."""
    out = []
    for r in records or []:
        conf = r.get("confidence")
        outcome = r.get("outcome")
        if conf is None or outcome not in (RIGHT, WRONG):
            continue
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            continue
        out.append((conf, outcome == RIGHT))
    return out


def evaluate_threshold(records, threshold: float) -> ReplayResult:
    """Replay a single confidence threshold over the judged records."""
    res = ReplayResult(threshold=float(threshold))
    for conf, is_right in _judged(records):
        acted = conf >= threshold
        if acted and is_right:
            res.kept_right += 1
        elif acted and not is_right:
            res.kept_wrong += 1
        elif not acted and is_right:
            res.suppressed_right += 1
        else:
            res.suppressed_wrong += 1
    return res


def _default_grid() -> list:
    return [round(i / 20.0, 3) for i in range(21)]  # 0.00 … 1.00, step 0.05


def sweep(records, thresholds=None) -> list:
    """Replay a range of thresholds; returns one ReplayResult per threshold."""
    grid = thresholds if thresholds is not None else _default_grid()
    return [evaluate_threshold(records, t) for t in grid]


def recommend_threshold(records, min_samples: int = DEFAULT_MIN_SAMPLES,
                        thresholds=None) -> Optional[dict]:
    """The threshold with the best accuracy over history, or None if there
    aren't yet enough judged samples to trust a recommendation.

    Ties break toward the *lower* threshold (act more readily) so the
    recommendation doesn't silently make JARVIS more conservative than the
    evidence requires. Returns a summary dict including the current-vs-best
    comparison the caller can act on."""
    judged = _judged(records)
    if len(judged) < max(1, min_samples):
        return None
    results = sweep(records, thresholds)
    # best accuracy, lowest threshold on a tie
    best = max(results, key=lambda r: (r.accuracy, -r.threshold))
    return {
        "samples": len(judged),
        "recommended": best.to_dict(),
        "sweep": [r.to_dict() for r in results],
    }


def replay_kind(kind: str, min_samples: int = DEFAULT_MIN_SAMPLES,
                limit: int = 2000, db_path: Optional[str] = None) -> dict:
    """Pull recent records of `kind` from the Decision Record and recommend a
    threshold. DB-facing convenience over the pure functions above; safe to call
    with no data (returns a 'not enough data' summary rather than raising)."""
    try:
        from . import decision_record
        records = decision_record.recent(limit=limit, kind=kind, db_path=db_path)
    except Exception:
        records = []
    rec = recommend_threshold(records, min_samples=min_samples)
    if rec is None:
        judged = len(_judged(records))
        return {
            "kind": kind,
            "ready": False,
            "samples": judged,
            "needed": min_samples,
            "reason": f"only {judged} judged decision(s); need {min_samples} to recommend a threshold",
        }
    return {"kind": kind, "ready": True, **rec}
