"""Proactive household intelligence (roadmap Phase P) — pure model primitive.

Household-level anticipation: an occupancy **rhythm** (how likely the home is
occupied in each daypart) and a **routine graph** (recurring activity sequences),
derived from plain observation rows. Its output is **suggestions, never actions**
— the invariant is structural: :func:`anticipate` emits ``Suggestion`` objects
that carry no actuator and are explicitly advisory, so proactivity here can only
*propose*. Acting on a suggestion stays the job of the authority/actuation seam.

Pure: no Home Assistant import, no I/O, no clock — observations (with their
injected daypart/weekday) are passed in, and every derivation is total and
deterministic. The live binder is ``proactive_audio`` (Phase P shadow+parity): its
audit tick folds each occupancy sample into this model, logs what it would
anticipate, and records a log-only agreement tally of this model's "proactivity
warranted?" verdict against the existing PredictiveHabitMatrix — observe-only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Canonical dayparts (reuse the space_time vocabulary conceptually).
DAYPARTS = ("night", "morning", "afternoon", "evening")


def _norm(value) -> str:
    return str(value or "").strip().lower()


def _clamp01(x) -> float:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f < 0.0 else 1.0 if f > 1.0 else f


@dataclass(frozen=True)
class Observation:
    """One recorded moment of household activity. ``daypart`` + ``weekday`` locate
    it in the weekly rhythm; ``occupied`` is whether anyone was home; ``activity``
    is an optional label used to mine routines. Timestamps are the caller's job —
    the primitive holds no clock. Total."""

    daypart: str = ""
    weekday: Optional[int] = None     # 0=Mon … 6=Sun, or None
    occupied: bool = False
    activity: str = ""

    def __post_init__(self):
        object.__setattr__(self, "daypart", _norm(self.daypart))
        object.__setattr__(self, "activity", _norm(self.activity))
        object.__setattr__(self, "occupied", bool(self.occupied))
        wd = self.weekday
        object.__setattr__(self, "weekday", int(wd) if isinstance(wd, int) else None)


@dataclass(frozen=True)
class Suggestion:
    """An advisory, model-sourced proactive suggestion. It carries **no actuator**
    and is explicitly advisory — the household model may only propose; whether to
    act is the authority/actuation seam's call."""

    kind: str                    # "comfort" | "routine" | "presence" | …
    message: str
    confidence: float = 0.0
    advisory: bool = True        # structural: a household suggestion never acts

    def to_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message,
                "confidence": round(self.confidence, 4), "advisory": True}


def occupancy_rhythm(observations) -> Dict[str, float]:
    """Likelihood the home is occupied in each daypart = occupied / total for that
    daypart, over the observations. Dayparts with no data are omitted. Total."""
    tally: Dict[str, List[int]] = {}
    for o in (observations or []):
        if not isinstance(o, Observation) or not o.daypart:
            continue
        slot = tally.setdefault(o.daypart, [0, 0])   # [occupied, total]
        slot[1] += 1
        if o.occupied:
            slot[0] += 1
    return {dp: round(occ / tot, 4) for dp, (occ, tot) in tally.items() if tot}


def routines(observations, *, min_support: int = 2) -> List[Tuple[str, str, int]]:
    """Mine recurring consecutive activity transitions ``(a → b)`` that occur at
    least ``min_support`` times, strongest first. A deterministic, bounded routine
    graph — the live layer can enrich it. Returns ``(from, to, support)``. Total."""
    acts = [o.activity for o in (observations or [])
            if isinstance(o, Observation) and o.activity]
    counts: Dict[Tuple[str, str], int] = {}
    for a, b in zip(acts, acts[1:]):
        if a and b:
            counts[(a, b)] = counts.get((a, b), 0) + 1
    out = [(a, b, n) for (a, b), n in counts.items() if n >= max(1, int(min_support))]
    out.sort(key=lambda t: (-t[2], t[0], t[1]))
    return out


def anticipate(observations, *, daypart: str = "",
               last_activity: str = "", min_support: int = 2) -> List[Suggestion]:
    """Derive **advisory** suggestions for the current context from the rhythm +
    routine graph. Never emits an action — every item is a ``Suggestion`` (carries
    no actuator). Total; never raises.

    - a presence suggestion when the current daypart is usually occupied;
    - a routine suggestion for the most-supported activity that usually follows
      ``last_activity``.
    """
    obs = [o for o in (observations or []) if isinstance(o, Observation)]
    out: List[Suggestion] = []
    dp = _norm(daypart)

    rhythm = occupancy_rhythm(obs)
    if dp and rhythm.get(dp, 0.0) >= 0.5:
        out.append(Suggestion(
            kind="presence",
            message=f"home is usually occupied in the {dp}",
            confidence=_clamp01(rhythm[dp])))

    la = _norm(last_activity)
    if la:
        follow = [(b, n) for (a, b, n) in routines(obs, min_support=min_support) if a == la]
        if follow:
            b, n = max(follow, key=lambda t: t[1])
            total = sum(n2 for _b, n2 in follow)
            out.append(Suggestion(
                kind="routine",
                message=f"after '{la}', '{b}' usually follows",
                confidence=_clamp01(n / total) if total else 0.0))
    return out


def summarize(observations) -> dict:
    """Compact roll-up for logging/diagnostics. Never raises."""
    obs = [o for o in (observations or []) if isinstance(o, Observation)]
    rhythm = occupancy_rhythm(obs)
    rt = routines(obs)
    return {
        "observations": len(obs),
        "dayparts_modeled": len(rhythm),
        "routines": len(rt),
        "top_routine": (f"{rt[0][0]}→{rt[0][1]}" if rt else None),
    }
