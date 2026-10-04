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
