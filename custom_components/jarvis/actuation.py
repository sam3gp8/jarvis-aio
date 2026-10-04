"""Universal actuation envelope (MCU Phase B).

The golden-path wiring Phase A proved on ``control_device``, extracted so every
actuator tool routes its device changes through the *same* kernel contract
instead of re-implementing it:

    context (WorldModel)  →  request (ActuatorRequest)  →  plan_shadow (kernel.plan)
          →  [caller performs the HA service call]  →  emit_event (JarvisEvent)
          →  outcome (ActuatorOutcome, via the caller's verify step)

This module is pure glue over the ``kernel`` primitives — no new behaviour. It
is **best-effort**: every function degrades to a null result and never raises
into a caller's authoritative path, so wiring a path onto the envelope can never
break that path. Authority stays **log-only**: the envelope *describes and
records* an actuation; it does not perform the service call (the caller does) or
gate it. Promoting any of this to enforcement is a separate, owner-gated step.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence

_LOGGER = logging.getLogger(__name__)

# ── agency self-limits shadow (MCU Phase E/E4) ──────────────────────────────────
# The kernel loop detector and agency budget watch JARVIS's *own* actuation rate:
# the detector notices an action thrashing (A → event → A, or flapping), the
# budget counts autonomous actions against a self-imposed hourly ceiling. Here
# they run in SHADOW — every actuation is fed in and a loop / exhausted-budget
# verdict is logged, but the verdict is NEVER acted on: no action is suppressed
# or deferred. Promoting this to ENFORCE (actually blocking an actuation) gates
# live actuation and is an owner-gated step, deliberately not taken.
_loop_detector = None
_agency_budget = None

# ── agency budget ENFORCE (MCU Phase G/G1) ──────────────────────────────────────
# First *decision* primitive promoted from shadow to enforce on the live home,
# chosen as the safest first trial: it gates ONLY a *discretionary autonomous*
# actuation — JARVIS acting on a learned/trusted pattern of its own accord
# (cognitive_core._execute_action_data). It does NOT see:
#   * user-requested actuations (the agent tool path / control_device), nor
#   * safety-critical responses (nighttime lockdown, intrusion securing — those
#     call hass.services directly and never route through this gate),
# so neither a user command nor a safety action can ever be budget-blocked.
#
# KILL-SWITCH: flip AGENCY_BUDGET_ENFORCE to False to revert instantly to
# shadow (log-only, zero behaviour change) on the next load — a one-line revert,
# no other edit required. Tests pass `enforce=` explicitly.
#
# The gate FAILS OPEN: any internal error allows the action, so a budget bug can
# never stop JARVIS from acting. The ceiling is the kernel default (60 autonomous
# actions / rolling hour) — generous for the autonomous-only population, so it
# only ever trips on a genuine runaway (feedback loop / over-eager pattern).
AGENCY_BUDGET_ENFORCE = True

# A DEDICATED budget counting only discretionary autonomous actuations — a
# different, correctly-scoped population from the envelope-wide _agency_budget
# shadow lens below (which still watches the whole actuation stream for thrash).
_autonomous_budget = None

# ── loop-detect ENFORCE (MCU Phase G/G2) ────────────────────────────────────────
# The second staged flip, same tightly-scoped path as G1: a thrashing discretionary
# autonomous actuation (the same action re-firing in a tight window, or an
# A -> event -> A self-trigger) is actually SUPPRESSED, not merely logged. Same
# exclusions as G1 — user-requested and safety-critical actuations never reach it.
# HIGH threshold on purpose (5 firings of the identical action within 60s, well
# above any legitimate proactive cadence) so a real repeat is never mistaken for a
# loop. FAILS OPEN; one-line kill-switch LOOP_DETECT_ENFORCE reverts to shadow.
LOOP_DETECT_ENFORCE = True
_autonomous_loop = None


def loop_detect_check(key: str, *, enforce: Optional[bool] = None,
                      cause: Optional[str] = None,
                      event_id: Optional[str] = None,
                      now: Optional[float] = None) -> tuple:
    """ENFORCE gate (G2): is this *discretionary autonomous* actuation thrashing?

    Records the firing into a dedicated high-threshold loop detector and returns
    ``(allowed, reason)``. When the detector reports an active loop or a post-loop
    cooldown and enforce is on, returns ``(False, reason)`` so the caller
    SUPPRESSES the actuation (breaking the cycle). When the kill-switch is off it
    logs a would-suppress and returns ``(True, reason)`` (shadow). FAILS OPEN —
    any internal error returns ``(True, "error")`` so a detector fault can never
    block JARVIS. Only the autonomous proactive path calls this.
    """
    global _autonomous_loop
    if enforce is None:
        enforce = LOOP_DETECT_ENFORCE
    try:
        import time
        from .kernel import loop_detect as _ld
        if _autonomous_loop is None:
            _autonomous_loop = _ld.LoopDetector(
                window_s=60.0, max_repeats=5, cooldown_s=60.0)
        t = time.time() if now is None else now
        verdict = _autonomous_loop.record(key, now=t, cause=cause,
                                          event_id=event_id)
        if verdict:         # looping or cooling down from a just-flagged loop
            if enforce:
                _LOGGER.warning(
                    "loop detect: autonomous actuation %s thrashing (%s, count=%s) "
                    "— SUPPRESSING this action (enforce, G2)",
                    key, verdict.reason, verdict.count)
                return False, verdict.reason
            _LOGGER.warning(
                "loop detect: autonomous actuation %s thrashing (%s) — NOT "
                "suppressed (kill-switch off / shadow)", key, verdict.reason)
            return True, verdict.reason
        return True, _ld.NONE
    except Exception:   # pragma: no cover - defensive, fail open
        return True, "error"


def agency_budget_check(*, enforce: Optional[bool] = None,
                        now: Optional[float] = None) -> tuple:
    """ENFORCE gate (G1): may one more *discretionary autonomous* actuation
    happen now, against the self-imposed hourly ceiling?

    Returns ``(allowed, remaining)``. Records against the budget only when the
    action is allowed (a blocked action consumes no slot). When the ceiling is
    reached: returns ``(False, 0)`` under enforce (the caller must skip the
    actuation), or ``(True, 0)`` when the kill-switch is off (shadow — logs a
    would-block and lets it through). FAILS OPEN — any internal error returns
    ``(True, None)`` so a budget fault can never block JARVIS.

    Only the autonomous proactive path calls this; user-requested and
    safety-critical actuations never reach it.
    """
    global _autonomous_budget
    if enforce is None:
        enforce = AGENCY_BUDGET_ENFORCE
    try:
        import time
        from .kernel import budget as _bg
        if _autonomous_budget is None:
            _autonomous_budget = _bg.AgencyBudget()
        t = time.time() if now is None else now
        verdict = _autonomous_budget.check_and_record(_bg.ACTION, now=t)
        if not verdict:
            cap = _autonomous_budget.limits.rate_for(_bg.ACTION)
            if enforce:
                _LOGGER.warning(
                    "agency budget: autonomous-action ceiling reached (%s/hr) — "
                    "BLOCKING this autonomous action (enforce, G1)", cap)
                return False, 0
            _LOGGER.warning(
                "agency budget: autonomous-action ceiling reached (%s/hr) — "
                "NOT blocked (kill-switch off / shadow)", cap)
            return True, 0
        return True, verdict.remaining
    except Exception:   # pragma: no cover - defensive, fail open
        return True, None


def _agency_shadow(capability: str, entity_id: str, *,
                   cause: Optional[str] = None,
                   event_id: Optional[str] = None) -> None:
    """Feed one actuation into the kernel loop detector + agency budget and log
    any thrash-loop or exhausted-budget verdict. SHADOW / log-only: best-effort,
    never suppresses an action, never raises into the actuation path."""
    global _loop_detector, _agency_budget
    try:
        import time
        from .kernel import loop_detect as _ld, budget as _bg
        if _loop_detector is None:
            _loop_detector = _ld.LoopDetector()
        if _agency_budget is None:
            _agency_budget = _bg.AgencyBudget()
        now = time.time()
        key = f"{capability}:{entity_id}"
        verdict = _loop_detector.record(key, now=now, cause=cause,
                                        event_id=event_id)
        if verdict.looping:
            _LOGGER.warning(
                "agency shadow: actuation loop on %s (%s, count=%s) — NOT "
                "suppressed (shadow)", key, verdict.reason, verdict.count)
        bverdict = _agency_budget.check_and_record(_bg.ACTION, now=now)
        if not bverdict:
            _LOGGER.warning(
                "agency shadow: autonomous-action budget exhausted at %s — NOT "
                "enforced (shadow)", key)
    except Exception:   # pragma: no cover - defensive
        pass


def correlation_id() -> Optional[str]:
    """Current kernel correlation id, or None — never raises."""
    try:
        from .kernel import correlation
        return correlation.current()
    except Exception:   # pragma: no cover - defensive
        return None


def context(hass, entity_id: str) -> Dict[str, Any]:
    """Pre-action context snapshot through the kernel WorldModel facade — the
    canonical context authority (entity_id / domain / name / state / area). The
    raw HA state is kept alongside for the post-action verify/read-back. Returns
    a dict that is always safe to read; ``exists`` is False when the entity is
    unknown to both the facade and raw state."""
    snap = None
    try:
        from .kernel.world_model import WorldModel
        snap = WorldModel(hass).device(entity_id)
    except Exception:   # pragma: no cover - defensive
        snap = None
    raw = None
    try:
        raw = hass.states.get(entity_id)
    except Exception:   # pragma: no cover - defensive
        raw = None
    prev = snap["state"] if snap else (raw.state if raw is not None else "unknown")
    area = snap.get("area") if snap else None
    return {"snapshot": snap, "raw": raw, "prev_state": prev, "area": area,
            "exists": bool(snap or raw is not None)}


def request(capability: str, entity_id: str, *, params: Optional[dict] = None,
            action: str = "", intent: Optional[str] = None,
            expected: Optional[Sequence[str]] = None):
    """Canonical ActuatorRequest for one actuation (carrying the expected
    end-state when known), or None best-effort."""
    try:
        from .kernel import build_actuator_request
        return build_actuator_request(
            capability, target=entity_id, params=dict(params or {}),
            intent=intent or (action.replace("_", " ") if action else None),
            correlation_id=correlation_id(),
            idempotency_key=f"{entity_id}:{action}" if action else None,
            expected_outcome="/".join(expected) if expected else None)
    except Exception:   # pragma: no cover - defensive
        return None


def plan_shadow(capability: str, entity_id: str, action: str,
                expected: Optional[Sequence[str]] = None, *,
                correlation: Optional[str] = None):
    """Express the actuation as a canonical one-step kernel Plan and log it
    (SHADOW — execution stays with the caller). Returns the Plan, or None."""
    try:
        from .kernel.plan import Plan, Step
        step = Step(
            action=capability,
            params={"entity_id": entity_id},
            preconditions=(f"exists:{entity_id}",),
            postconditions=tuple(f"state:{e}" for e in (expected or ())),
            idempotency_key=f"{entity_id}:{action}")
        plan = Plan(goal=f"{action} {entity_id}", steps=(step,),
                    correlation_id=correlation if correlation is not None
                    else correlation_id())
        _LOGGER.debug("plan(shadow): goal=%s steps=%s",
                      plan.goal, [s.action for s in plan.steps])
        return plan
    except Exception:   # pragma: no cover - defensive
        return None


def emit_event(hass, capability: str, entity_id: str, *, action: str = "",
               area: Optional[str] = None, request=None) -> None:
    """Publish a canonical actuation JarvisEvent on the kernel event bus (the
    ledger records it). Best-effort; never raises."""
    try:
        from . import events
        from .kernel import from_actuation
        ev = from_actuation(
            capability, target=entity_id,
            intent=action.replace("_", " ") if action else None,
            location=area,
            request_id=(request.id if request is not None else None),
            correlation_id=correlation_id())
        events.publish(hass, ev)
        # E4: feed the actuation to the agency loop/budget watchers (shadow).
        _agency_shadow(capability, entity_id, cause=correlation_id(),
                       event_id=ev.id)
    except Exception:   # pragma: no cover - defensive
        pass


def outcome(request, status: str, hass, entity_id: str, detail: str = "") -> None:
    """Record the canonical ActuatorOutcome for a verified actuation (the
    audit's point 18 — "the service returned success" is not "the world reached
    the expected state"). Best-effort; never raises."""
    try:
        from .kernel.actuator import ActuatorOutcome
        st = hass.states.get(entity_id)
        oc = ActuatorOutcome(
            request_id=(request.id if request is not None else f"{entity_id}:actuation"),
            status=status,
            observed=(str(st.state) if st is not None else None),
            detail=detail)
        _LOGGER.debug("actuator(outcome): %s", oc.to_dict())
    except Exception:   # pragma: no cover - defensive
        pass
