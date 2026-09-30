"""
JARVIS adaptive feedback — close the LEARN loop for every proactive surface.

The Decision Record (decision_record.py) already stores an immutable row per
proactive decision and, later, a single ``outcome`` verdict captured from real
signals (a dismissal, a false alarm, a welcome). ``decision_record.outcome_rate``
turns those verdicts into a good/unwelcome breakdown per decision *kind*.

Historically only the automation-suggestion surface fed that back into how
selective it was (pattern_analyzer._learned_threshold_delta). This module
generalises the same idea so *any* proactive surface — anticipation alerts,
intrusion escalation, suggestions — can steer its own confidence/salience bar
from how its past output was received:

    unwelcome recently  → raise the bar (be more selective)
    almost all welcome  → lower the bar a little (be more generous)

Design constraints, mirrored from the suggestion path so behaviour is uniform
and safe:

  • Opt-in.  Off unless the caller's opt-in config flag is set. Returns a 0.0
    delta otherwise, so wiring a surface up is a no-op until the user enables it.
  • Hysteresis.  Needs a minimum number of *judged* records before it moves at
    all, so a single dismissal can't swing the bar.
  • Bounded + clamped.  Deltas are small and the effective threshold is clamped
    to a sane range by the caller.
  • Cached.  Each kind's delta is recomputed at most every few minutes; the DB
    read is cheap but this keeps it off the hot path entirely.
  • Never raises.  Any error yields a 0.0 delta — feedback must never break the
    decision it is trying to improve.

Pure apart from the decision_record read and the opt-in lookup, so it is
directly unit-testable with an injected db_path.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# Defaults shared with the suggestion surface (pattern_analyzer._ADAPT_*).
DEFAULT_WINDOW_S = 30 * 86400.0     # look back a month
DEFAULT_MIN_JUDGED = 5              # need this many judged records before moving
_CACHE_TTL = 300.0                 # recompute a kind's delta at most every 5 min

# kind -> {"ts": float, "delta": float}
_CACHE: dict[str, dict] = {}


def _opt_in(opt_in_key: Optional[str]) -> bool:
    """True when the caller passed no gate (always on) or the config flag is set."""
    if not opt_in_key:
        return True
    try:
        from . import jarvis_config
        return bool(jarvis_config.get(opt_in_key, False))
    except Exception:
        return False


def _delta_from_rate(unwelcome_rate: float) -> float:
    """Map an unwelcome rate to a threshold delta. Identical shape to the
    suggestion surface: much stricter when mostly unwelcome, a touch more
    generous when almost everything lands well."""
    if unwelcome_rate >= 0.5:
        return 0.15         # mostly unwelcome → much more selective
    if unwelcome_rate >= 0.3:
        return 0.07         # somewhat unwelcome → more selective
    if unwelcome_rate <= 0.1:
        return -0.07        # almost all welcome → a little more generous
    return 0.0


def threshold_delta(
    kind: str,
    *,
    opt_in_key: Optional[str] = None,
    window_s: float = DEFAULT_WINDOW_S,
    min_judged: int = DEFAULT_MIN_JUDGED,
    db_path: Optional[str] = None,
) -> float:
    """Learned adjustment to a proactive surface's confidence/salience threshold
    for one decision ``kind``, from how its recent decisions were received.

    Positive → be more selective; negative → be more generous. Returns 0.0 when
    the opt-in is off, on any error, or with too little judged evidence. Cached
    per kind for a few minutes."""
    if not _opt_in(opt_in_key):
        return 0.0
    now = time.time()
    cached = _CACHE.get(kind)
    if cached and now - cached["ts"] < _CACHE_TTL:
        return cached["delta"]
    delta = 0.0
    try:
        from . import decision_record
        r = decision_record.outcome_rate(kind, window_s=window_s, db_path=db_path)
        if int(r.get("judged", 0)) >= int(min_judged):
            delta = _delta_from_rate(float(r.get("unwelcome_rate") or 0.0))
    except Exception as exc:
        _LOGGER.debug("feedback.threshold_delta(%s) error: %s", kind, exc)
        delta = 0.0
    _CACHE[kind] = {"ts": now, "delta": delta}
    return delta


def effective_threshold(
    base: float,
    kind: str,
    *,
    lo: float = 0.3,
    hi: float = 0.95,
    opt_in_key: Optional[str] = None,
    window_s: float = DEFAULT_WINDOW_S,
    min_judged: int = DEFAULT_MIN_JUDGED,
    db_path: Optional[str] = None,
) -> float:
    """``base`` threshold adjusted by the learned delta for ``kind`` and clamped
    to [lo, hi]. When feedback is off or has no evidence this returns ``base``
    unchanged (clamped), so it is always safe to route a threshold through."""
    try:
        b = float(base)
    except (TypeError, ValueError):
        return base
    d = threshold_delta(kind, opt_in_key=opt_in_key, window_s=window_s,
                        min_judged=min_judged, db_path=db_path)
    return min(hi, max(lo, b + d))


def reset_cache() -> None:
    """Clear the per-kind delta cache (used by tests)."""
    _CACHE.clear()
