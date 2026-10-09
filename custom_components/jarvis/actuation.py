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

import json
import logging
import os
import time
from collections import deque
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


def acting_agent() -> str:
    """The agent acting in the current context — ``"jarvis"`` on the direct path,
    the sub-agent (e.g. ``"friday"``) while a delegated run is in scope (H6).
    Never raises; always returns a non-empty name."""
    try:
        from .kernel import actor
        return actor.current()
    except Exception:   # pragma: no cover - defensive
        return "jarvis"


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
            actor=acting_agent(),
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
            actor=(getattr(request, "actor", None) or acting_agent()),
            location=area,
            request_id=(request.id if request is not None else None),
            correlation_id=correlation_id())
        events.publish(hass, ev)
        # E4: feed the actuation to the agency loop/budget watchers (shadow).
        _agency_shadow(capability, entity_id, cause=correlation_id(),
                       event_id=ev.id)
    except Exception:   # pragma: no cover - defensive
        pass


# SHADOW: the Epistemic-Fabric outcome primitive (kernel.outcome) is emitted
# alongside the canonical ActuatorOutcome below — observe-only, nothing reads it
# yet (Phase M will). Flip OUTCOME_SHADOW to False to silence it instantly. The
# kernel.actuator ActuatorOutcome recording is unchanged either way. (#237)
OUTCOME_SHADOW = True


def _record_outcome_shadow(request, status: str, observed, detail: str, hass=None) -> None:
    """Emit a structured kernel.outcome.Outcome for a verified actuation (shadow).

    Derived from the same verification verdict that drives the ActuatorOutcome:
    ``status == "verified"`` is the success verdict; the intended end-state comes
    from the request's postconditions, the observed state from the read-back.
    Log-only, defensive, never raises — a learning (Phase M) consumer will read
    these once the primitive earns parity, not this path."""
    try:
        from .kernel import outcome as _koutcome
        try:
            from .kernel.actuator import VERIFIED
        except Exception:   # pragma: no cover - defensive
            VERIFIED = "verified"
        expected = getattr(request, "expected_outcome", None)
        intended = (f"state:{expected}" if expected
                    else (detail or getattr(request, "capability", "") or ""))
        observed_str = str(observed) if observed is not None else (detail or "")
        oc = _koutcome.from_verification(
            intended_result=intended,
            observed_result=observed_str,
            verified=(status == VERIFIED),
            confidence=1.0,                      # direct read-back, not inferred
            actor=str(getattr(request, "actor", "") or ""),
            capability=str(getattr(request, "capability", "") or ""),
            correlation_id=str(getattr(request, "correlation_id", "") or ""),
        )
        _LOGGER.debug("outcome(shadow): %s", oc.to_dict())
        # Phase M (shadow + parity): feed the outcome into the learning loop and
        # log the would-be per-capability trust adjustment, then compare it to the
        # realized success rate — both observe-only, drive nothing.
        _emit_learning_shadow(oc)
        _emit_learning_parity(oc)
        # Phase N (shadow + parity): log the would-be graduated-autonomy level
        # this capability has earned from the same rolling window, then compare
        # whether it would auto-execute with the blanket mode flag — observe-only.
        _emit_autonomy_shadow(oc)
        _emit_autonomy_parity(oc)
        # Phase M enforce (default OFF): close the loop — persist the learned
        # per-capability trust and feed it back as the next prior.
        _emit_learning_enforce(oc, hass)
    except Exception:   # pragma: no cover - defensive
        pass


# SHADOW (Phase M — Learning & Adaptation): keep a bounded window of recent
# kernel.outcome.Outcomes and, per verified actuation, log the *would-be*
# per-capability trust adjustment the learning primitive (kernel.learning) would
# compute from that capability's track record. Observe-only — nothing consumes
# the adjustment; the real per-capability trust store arrives with Phase N, and
# applying adjustments is the owner-gated LEARNING_ENFORCE rung. The prior is a
# neutral 0.5 until that store exists. Set LEARNING_SHADOW = False to silence it.
LEARNING_SHADOW = True
_LEARN_WINDOW = 50                 # recent outcomes retained for the rollup
_CAP_TRUST_PRIOR = 0.5             # neutral prior (no real trust store until Phase N)
_recent_outcomes: deque = deque(maxlen=_LEARN_WINDOW)


def _emit_learning_shadow(oc) -> None:
    """Phase M shadow: roll up recent outcomes for this outcome's capability and
    log the would-be trust WeightAdjustment. Best-effort, never raises."""
    if not LEARNING_SHADOW:
        return
    try:
        _recent_outcomes.append(oc)
        cap = getattr(oc, "capability", "") or ""
        if not cap:
            return
        from .kernel import learning
        cap_outcomes = [o for o in _recent_outcomes
                        if (getattr(o, "capability", "") or "") == cap]
        adj = learning.adjust(cap, _CAP_TRUST_PRIOR, cap_outcomes)
        if adj.samples == 0:
            return
        _LOGGER.debug(
            "learning(shadow): capability=%s trust %.2f -> %.2f "
            "(delta %+.2f, signal %.2f, n=%d)%s",
            cap, adj.prior, adj.proposed, adj.delta, adj.signal, adj.samples,
            " [clamped]" if adj.clamped else "")
    except Exception:   # pragma: no cover - defensive
        pass


# PARITY (Phase M): offline-compare the learned per-capability trust against the
# capability's realized success rate and log AGREEMENT/DIVERGENCE — observe-only.
# The learned trust (confidence-weighted learning signal, nudged from the 0.5
# prior) should point the same way as the raw success rate; a divergence flags a
# capability whose confidence-weighting disagrees with its hit rate, which is
# exactly what the enforce rung would need to get right before it trusts the
# learned value. Nothing consumes it; set LEARNING_PARITY = False to silence it.
LEARNING_PARITY = True


def _emit_learning_parity(oc) -> None:
    """Phase M parity: compare the would-be learned trust for this outcome's
    capability against its realized success rate and log agreement. Best-effort,
    never raises; reads the same rolling window, drives nothing."""
    if not LEARNING_PARITY:
        return
    try:
        cap = getattr(oc, "capability", "") or ""
        if not cap:
            return
        from .kernel import learning, outcome as _koutcome
        cap_outcomes = [o for o in _recent_outcomes
                        if (getattr(o, "capability", "") or "") == cap]
        stats = _koutcome.summarize(cap_outcomes)
        if stats.count == 0:
            return
        trust = learning.adjust(cap, _CAP_TRUST_PRIOR, cap_outcomes).proposed
        success_rate = stats.success_rate
        agree = (trust >= _CAP_TRUST_PRIOR) == (success_rate >= 0.5)
        _LOGGER.debug(
            "learning(parity): capability=%s trust=%.2f success_rate=%.2f "
            "agree=%s (n=%d)", cap, trust, success_rate, agree, stats.count)
    except Exception:   # pragma: no cover - defensive
        pass


# ENFORCE (Phase M — Learning & Adaptation, owner-gated): close the loop. The
# per-capability trust the shadow/parity only *logged* is now PERSISTED and fed
# back as the prior for the next adjustment, so trust accumulates across restarts
# instead of resetting to the neutral 0.5 each tick. `learned_trust(cap)` exposes
# the stored value for downstream consumers (e.g. graduated autonomy). Kill-
# switched, default OFF (LEARNING_ENFORCE / the `learning_enforce` config key) so
# shipping is behaviour-preserving — the prior stays 0.5 and the store is never
# written; fail-safe — any error falls back to the neutral prior and persists
# nothing. Stored to <config>/jarvis/capability_trust.json (house JSON-snapshot
# pattern), loaded once, saved throttled.
LEARNING_ENFORCE = False
_LEARN_STORE: Dict[str, float] = {}
_LEARN_STORE_LOADED = False
_LEARN_STORE_PATH: Optional[str] = None
_LEARN_LAST_SAVE = 0.0
_LEARN_SAVE_THROTTLE = 60.0        # seconds; outcome() is not hot, but don't thrash the disk


def _learning_enforce_on() -> bool:
    """True when the learning enforce flip is active (module flag or the
    `learning_enforce` config key). Never raises."""
    if LEARNING_ENFORCE:
        return True
    try:
        from . import jarvis_config
        return bool(jarvis_config.get("learning_enforce", False))
    except Exception:
        return False


def learned_trust(cap: str) -> float:
    """The persisted learned trust for a capability in [0, 1], or the neutral
    prior when the loop is open / the capability is unseen. Never raises."""
    try:
        return float(_LEARN_STORE.get(cap, _CAP_TRUST_PRIOR))
    except Exception:
        return _CAP_TRUST_PRIOR


def _learn_store_load(hass) -> None:
    global _LEARN_STORE_LOADED, _LEARN_STORE_PATH
    if _LEARN_STORE_LOADED:
        return
    _LEARN_STORE_LOADED = True
    try:
        _LEARN_STORE_PATH = hass.config.path("jarvis", "capability_trust.json")
        if os.path.exists(_LEARN_STORE_PATH):
            with open(_LEARN_STORE_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    try:
                        _LEARN_STORE[str(k)] = max(0.0, min(1.0, float(v)))
                    except (TypeError, ValueError):
                        pass
    except Exception:   # pragma: no cover - defensive
        pass


def _learn_store_save(hass, *, force: bool = False) -> None:
    global _LEARN_LAST_SAVE
    try:
        now = time.time()
        if not force and (now - _LEARN_LAST_SAVE) < _LEARN_SAVE_THROTTLE:
            return
        path = _LEARN_STORE_PATH or hass.config.path("jarvis", "capability_trust.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_LEARN_STORE, f, indent=2)
        _LEARN_LAST_SAVE = now
    except Exception:   # pragma: no cover - defensive
        pass


def _emit_learning_enforce(oc, hass) -> None:
    """Phase M enforce: fold this outcome into the capability's PERSISTED trust
    (prior = the stored value, nudged by the kernel learner) and save it, so the
    next adjustment builds on it — a closed loop. Does nothing and persists
    nothing when the flip is off. Kill-switched + fail-safe; never raises."""
    if not _learning_enforce_on() or hass is None:
        return
    try:
        # Require a usable store path — never hold trust we can't persist.
        try:
            hass.config.path("jarvis", "capability_trust.json")
        except Exception:
            return
        cap = getattr(oc, "capability", "") or ""
        if not cap:
            return
        _learn_store_load(hass)
        from .kernel import learning
        cap_outcomes = [o for o in _recent_outcomes
                        if (getattr(o, "capability", "") or "") == cap]
        adj = learning.adjust(cap, learned_trust(cap), cap_outcomes)
        if adj.samples == 0:
            return
        _LEARN_STORE[cap] = max(0.0, min(1.0, float(adj.proposed)))
        _learn_store_save(hass)
        _LOGGER.debug("learning(enforce): capability=%s trust->%.3f persisted (n=%d)",
                      cap, _LEARN_STORE[cap], adj.samples)
    except Exception:   # pragma: no cover - defensive
        pass


# SHADOW (Phase N — Graduated Autonomy): per verified actuation, log the *would-be*
# autonomy level this capability has earned from the same rolling outcome window —
# a pure function of its track record (kernel.outcome.summarize) and its risk class
# (kernel.autonomy.grant). Observe-only: nothing consumes the level, and the global
# autonomy flag is untouched. Replacing that flag with the earned level is the
# owner-gated GRADUATED_AUTONOMY_ENFORCE rung (fail-safe = the current single
# setting). Set AUTONOMY_SHADOW = False to silence it.
AUTONOMY_SHADOW = True


def _emit_autonomy_shadow(oc) -> None:
    """Phase N shadow: roll up recent outcomes for this outcome's capability and
    log the would-be graduated-autonomy level kernel.autonomy would grant. Reads
    the same rolling window, drives nothing. Best-effort, never raises."""
    if not AUTONOMY_SHADOW:
        return
    try:
        cap = getattr(oc, "capability", "") or ""
        if not cap:
            return
        from .kernel import autonomy, outcome as _koutcome
        cap_outcomes = [o for o in _recent_outcomes
                        if (getattr(o, "capability", "") or "") == cap]
        stats = _koutcome.summarize(cap_outcomes)
        g = autonomy.grant(cap, stats)
        _LOGGER.debug(
            "autonomy(shadow): capability=%s risk=%s level=%s "
            "(n=%d, rate=%.2f)%s", cap, g.risk, g.level, g.samples,
            g.success_rate, " [pinned]" if g.pinned else "")
    except Exception:   # pragma: no cover - defensive
        pass


# PARITY (Phase N): offline-compare whether the would-be *earned* autonomy level
# for this capability would auto-execute (``AutonomyGrant.may_act``) against the
# single blanket incumbent it refines — the active mode's auto-actions flag
# (``modes.mode_allows_auto_actions``, "whether autonomy graduations may
# auto-execute"). A divergence flags a capability whose per-capability earned
# trust disagrees with the one-size-fits-all flag — exactly the resolution the
# owner-gated GRADUATED_AUTONOMY_ENFORCE rung buys by replacing the flag with the
# earned level. Per-proactive-pattern trust stays with cognitive_core's
# AutonomyManager; this axis is the blanket flag only. Observe-only; nothing
# consumes it. Set AUTONOMY_PARITY = False to silence it.
AUTONOMY_PARITY = True


def _emit_autonomy_parity(oc) -> None:
    """Phase N parity: compare the would-be earned autonomy level's auto-execute
    verdict for this capability against the blanket mode auto-actions flag and log
    agreement. Best-effort, never raises; reads the same window, drives nothing."""
    if not AUTONOMY_PARITY:
        return
    try:
        cap = getattr(oc, "capability", "") or ""
        if not cap:
            return
        from .kernel import autonomy, outcome as _koutcome
        cap_outcomes = [o for o in _recent_outcomes
                        if (getattr(o, "capability", "") or "") == cap]
        stats = _koutcome.summarize(cap_outcomes)
        g = autonomy.grant(cap, stats)
        try:
            from . import modes
            auto_flag = bool(modes.mode_allows_auto_actions())
        except Exception:   # pragma: no cover - defensive
            return
        earned_auto = g.may_act
        agree = (earned_auto == auto_flag)
        _LOGGER.debug(
            "autonomy(parity): capability=%s earned=%s earned_auto=%s "
            "mode_auto_flag=%s agree=%s (n=%d)", cap, g.level, earned_auto,
            auto_flag, agree, g.samples)
    except Exception:   # pragma: no cover - defensive
        pass


def outcome(request, status: str, hass, entity_id: str, detail: str = "") -> None:
    """Record the canonical ActuatorOutcome for a verified actuation (the
    audit's point 18 — "the service returned success" is not "the world reached
    the expected state"). Best-effort; never raises."""
    observed = None
    try:
        from .kernel.actuator import ActuatorOutcome
        st = hass.states.get(entity_id)
        observed = (str(st.state) if st is not None else None)
        oc = ActuatorOutcome(
            request_id=(request.id if request is not None else f"{entity_id}:actuation"),
            status=status,
            observed=observed,
            detail=detail)
        _LOGGER.debug("actuator(outcome): %s", oc.to_dict())
    except Exception:   # pragma: no cover - defensive
        pass
    # #237: emit the Epistemic-Fabric Outcome in shadow alongside the above.
    if OUTCOME_SHADOW:
        _record_outcome_shadow(request, status, observed, detail, hass)


async def execute_actuator(hass, *, capability: str, entity_id: str,
                           domain: str, service: str, data: dict,
                           action: str = "", areq=None,
                           area: Optional[str] = None, verify=None,
                           blocking: bool = True):
    """MCU Phase H (H1) — the **universal actuator seam**.

    One authoritative place every consequential actuation converges on, composing
    a single ActuatorRequest's **Execution → Event → (scheduled) Verification /
    Outcome** — instead of each path re-assembling the envelope by hand. This is
    the control_device golden path turned into the reusable framework the audit
    asked for; migrating paths call this rather than open-coding plan-execute +
    emit_event + verify.

    - Execution routes **through** ``kernel.plan.aexecute_plan`` (the plan
      contract: precondition → act), so the actuation genuinely passes the kernel
      planner. ``domain``/``service``/``data`` are the HA service call it performs.
    - On success the canonical actuation JarvisEvent is published (``emit_event``).
    - ``verify`` — optional 0-arg coroutine factory — is *scheduled* (not awaited)
      after a successful execute: the caller's verify-after-act, which records the
      terminal ActuatorOutcome. ``None`` → no verify (non-deterministic actions).
    - ``blocking`` — whether the HA service call waits for completion. Default
      True (control_device's verify-after-act semantics); bulk/fan-out callers
      pass False for fire-and-forget across many targets (no per-target verify).

    Returns ``(ok: bool, detail: str)``; a failed/blocked plan yields
    ``(False, detail)`` — the same error contract the inline path produced. Never
    raises for control flow."""
    from .kernel import plan as _kplan
    kp = _kplan.Plan(
        goal=(f"{action} {entity_id}".strip() or capability),
        steps=(_kplan.Step(
            action=capability, params=dict(data),
            preconditions=(f"exists:{entity_id}",),
            idempotency_key=(f"{entity_id}:{action}" if action else None)),),
        correlation_id=(getattr(areq, "correlation_id", None) or correlation_id()))

    async def _run(_s):
        await hass.services.async_call(domain, service, dict(data),
                                       blocking=blocking)
        return True

    async def _chk(_c, _s):
        return hass.states.get(entity_id) is not None

    rep = await _kplan.aexecute_plan(kp, run_step=_run, check=_chk)
    if not rep.ok:
        det = (rep.outcomes[0].detail if rep.outcomes else "") or "action failed"
        return False, det
    if capability:
        emit_event(hass, capability, entity_id, action=action, area=area,
                   request=areq)
    if verify is not None:
        try:
            hass.async_create_task(verify())
        except Exception:   # pragma: no cover - defensive
            pass
    return True, ""


# ── SAFETY policy-mode seam (MCU Phase H, H8) ───────────────────────────────────
# Securing the home against a threat (intrusion lockdown, nighttime lockdown) is
# the last actuator path that still called hass.services directly, bypassing the
# universal seam. H8 routes it through execute_actuator too — but under a
# FAIL-TOWARD-PROTECTION contract that is the opposite of the discretionary path:
#
#   * it must NEVER be blocked. It does not pass the agency-budget / loop-detect
#     gates at all (those live in the proactive path, not in the seam), so a
#     spent budget or a thrash verdict can never hold a safety response.
#   * verification is mandatory (a securing action that silently didn't land is a
#     safety failure), so it always schedules a verify-after-act + outcome.
#   * on ANY seam/kernel fault — or any non-success — it FAILS OPEN to a direct
#     hass.services call, so the home is still secured. lock / close_cover are
#     idempotent (locking a locked lock, closing a closed cover are no-ops), so
#     the backstop can never leave the home *less* secure than the direct call.
#   * a one-line kill-switch reverts the whole path to the exact direct call it
#     replaced.
SAFETY_SEAM_ENFORCE = True

# securing service -> the secured end-state we verify against
_SAFETY_EXPECTED = {"lock": ("locked",), "close_cover": ("closed",)}


async def _safety_verify(hass, areq, entity_id: str, service: str) -> None:
    """Record the verified ActuatorOutcome for a securing action — did the entity
    actually reach its secured state? Best-effort; never raises."""
    try:
        from .kernel.actuator import VERIFIED, MISMATCH
    except Exception:   # pragma: no cover - defensive
        VERIFIED, MISMATCH = "verified", "mismatch"
    try:
        expected = _SAFETY_EXPECTED.get(service, ())
        st = hass.states.get(entity_id)
        cur = str(st.state).lower() if st is not None else None
        status = VERIFIED if (cur in expected) else MISMATCH
        outcome(areq, status, hass, entity_id,
                detail=f"safety:{service} -> {cur}")
    except Exception:   # pragma: no cover - defensive
        pass


async def execute_safety_actuator(hass, *, capability: str, entity_id: str,
                                  domain: str, service: str,
                                  data: Optional[dict] = None,
                                  action: str = "") -> bool:
    """Perform a SAFETY/securing actuation through the universal seam, with the
    fail-toward-protection contract above. Returns True iff the securing action
    was performed (through the seam or the direct fallback). Never raises."""
    svc_data = dict(data or {"entity_id": entity_id})

    async def _direct() -> bool:
        await hass.services.async_call(domain, service, dict(svc_data),
                                       blocking=True)
        return True

    # Kill-switch → the exact direct call this path replaced.
    if not SAFETY_SEAM_ENFORCE:
        return await _direct()

    try:
        areq = request(capability, entity_id, params=svc_data, action=action,
                       expected=_SAFETY_EXPECTED.get(service))
    except Exception:   # pragma: no cover - defensive
        areq = None

    try:
        ok, _detail = await execute_actuator(
            hass, capability=capability, entity_id=entity_id, domain=domain,
            service=service, data=svc_data, action=action, areq=areq,
            verify=(lambda: _safety_verify(hass, areq, entity_id, service)),
            blocking=True)
        if ok:
            return True
        _LOGGER.warning(
            "safety seam did not secure %s (%s): %s — failing open to a direct "
            "call", entity_id, capability, _detail)
    except Exception as exc:
        _LOGGER.warning(
            "safety seam error on %s (%s): %s — failing open to a direct call",
            entity_id, capability, exc)

    # Fail toward protection: secure directly so a safety response is never
    # blocked by a seam fault. Idempotent, so this cannot un-secure anything.
    try:
        return await _direct()
    except Exception as exc:   # pragma: no cover - defensive
        _LOGGER.warning("safety direct call failed on %s (%s): %s",
                        entity_id, capability, exc)
        return False
