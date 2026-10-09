"""Planner → Executor → Verifier (kernel Phase 5, docs/KERNEL_PLAN.md).

Formalises what `goals.py` + the agent's execution do ad hoc into explicit plan
objects: each step carries **preconditions**, **postconditions** (verify-after-act
as a first-class step, not a bolt-on), and an **idempotency_key** so a retried or
replayed plan never double-acts. The executor checks preconditions, runs the step,
then verifies postconditions (with a single retry, mirroring the existing
verify-after-act), and stops on the first unmet gate.

Pure: no Home Assistant import and no I/O. The side-effecting parts — actually
performing a step, evaluating a condition against live state, knowing which
idempotency keys already ran — are **injected** callables, so the orchestration is
deterministic and unit-testable, and real callers wire it to HA. Phase 5 ships
this additively; `goals`/agent adoption follows.

Phase U — Advanced Reasoning & Planning (constraint-aware plans).
Two additive, behaviour-preserving extensions over the linear executor:

- **alternatives** — a step may carry ordered fallback `Step`s. If the primary
  attempt fails (its precondition is unmet, the action fails, or postconditions
  never verify), the executor tries each alternative in order; the first that
  reaches DONE satisfies the step. A step with no alternatives behaves exactly as
  before, so this is a pure addition.
- **compensation** — a step may declare an undo `Step`. The executor never runs
  it (a saga/rollback *engine* is deliberately out of scope for this deployment,
  docs/KERNEL_PLAN.md). Instead :func:`plan_compensation` *derives* the rollback
  plan — the compensations of the steps that actually took hold, newest-first — so
  a caller that chooses to unwind a partially-applied plan routes that plan back
  through :func:`execute_plan` / :func:`aexecute_plan`, i.e. through the same
  authority + verify gates as any other actuation. Pure representation and
  derivation, opt-in execution.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

_LOGGER = logging.getLogger(__name__)

# ── step outcomes ───────────────────────────────────────────────────────────────
PENDING = "pending"
SKIPPED = "skipped"              # idempotency_key already completed
BLOCKED = "blocked"             # a precondition was not met
DONE = "done"                  # ran and postconditions verified
FAILED = "failed"              # the action itself failed
VERIFY_FAILED = "verify_failed"  # ran, but postconditions never held


@dataclass(frozen=True)
class Step:
    """One unit of a plan.

    ``action`` is a capability string (e.g. ``"light.turn_on"``); ``params`` its
    arguments. ``preconditions`` / ``postconditions`` are opaque condition
    descriptors evaluated by the injected ``check`` callable, so they stay
    serialisable. ``idempotency_key`` makes re-execution safe.

    ``alternatives`` are ordered fallback steps tried (full act+verify) when the
    primary attempt fails; the first that reaches DONE satisfies this step.
    ``compensation`` is this step's undo action — never run automatically, only
    surfaced by :func:`plan_compensation` for an opt-in rollback. Both default to
    empty/None, so a step that declares neither behaves as it always has.
    """

    action: str
    params: dict = field(default_factory=dict)
    preconditions: Tuple[Any, ...] = ()
    postconditions: Tuple[Any, ...] = ()
    idempotency_key: Optional[str] = None
    alternatives: Tuple["Step", ...] = ()
    compensation: Optional["Step"] = None
    id: str = field(default_factory=lambda: "step_" + uuid.uuid4().hex)


@dataclass(frozen=True)
class Plan:
    goal: str
    steps: Tuple[Step, ...] = ()
    correlation_id: Optional[str] = None
    id: str = field(default_factory=lambda: "plan_" + uuid.uuid4().hex)
    created_ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class StepOutcome:
    step_id: str
    action: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class PlanReport:
    plan_id: str
    ok: bool
    outcomes: Tuple[StepOutcome, ...]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["outcomes"] = [asdict(o) for o in self.outcomes]
        return d


# Injected callables:
#   run_step(step)       -> truthy on success (performs the action)
#   check(condition, step) -> bool (evaluates one pre/postcondition against state)
RunStep = Callable[[Step], Any]
Check = Callable[[Any, Step], bool]


def _always_true(_condition: Any, _step: Step) -> bool:
    return True


def execute_plan(
    plan: Plan,
    *,
    run_step: RunStep,
    check: Optional[Check] = None,
    completed: Optional[Set[str]] = None,
    verify_retries: int = 1,
) -> PlanReport:
    """Run a plan step-by-step: skip already-done steps, gate on preconditions,
    act, then verify postconditions (retrying the action ``verify_retries`` times).

    Stops at the first step that is BLOCKED, FAILED or VERIFY_FAILED and reports
    ``ok=False``. ``completed`` (a set of idempotency keys) is consulted for skips
    and updated in place as steps succeed, so a re-run of the same plan no-ops the
    parts that already took hold.
    """
    check = check or _always_true
    done_keys: Set[str] = completed if completed is not None else set()
    outcomes: List[StepOutcome] = []
    ok = True

    for step in plan.steps:
        # 1. Idempotency: skip a step whose effect is already recorded.
        if step.idempotency_key and step.idempotency_key in done_keys:
            outcomes.append(StepOutcome(step.id, step.action, SKIPPED,
                                        "idempotency_key already completed"))
            continue

        # 2. Attempt the step (precondition-gate → act → verify); on failure fall
        #    back through its declared alternatives in order (Phase U). A step with
        #    no alternatives collapses to exactly the old act/verify behaviour.
        status, detail = _attempt(step, run_step, check, verify_retries)
        if status != DONE and step.alternatives:
            status, detail = _with_alternatives(
                step, status, detail, run_step, check, verify_retries)

        outcomes.append(StepOutcome(step.id, step.action, status, detail))
        if status != DONE:
            ok = False
            break
        if step.idempotency_key:
            done_keys.add(step.idempotency_key)

    return PlanReport(plan_id=plan.id, ok=ok, outcomes=tuple(outcomes))


def _attempt(step: Step, run_step: RunStep, check: Check,
             verify_retries: int) -> Tuple[str, str]:
    """Precondition-gate, act, then verify one step. Returns (status, detail).

    Folds the precondition check and the act/verify into one attempt so the
    primary step and each alternative go through identical gating.
    """
    if not _all_hold(check, step, step.preconditions):
        return BLOCKED, "precondition not met"
    return _run_and_verify(step, run_step, check, verify_retries)


def _with_alternatives(step: Step, primary_status: str, primary_detail: str,
                       run_step: RunStep, check: Check,
                       verify_retries: int) -> Tuple[str, str]:
    """Try each declared alternative after a failed primary attempt.

    The first alternative that reaches DONE satisfies the step (noting which
    one). If every alternative also fails, the *primary's* own (status, detail)
    is returned unchanged — so a step whose alternatives are exhausted reports
    the same failure it would have had without any.
    """
    for i, alt in enumerate(step.alternatives):
        status, detail = _attempt(alt, run_step, check, verify_retries)
        if status == DONE:
            note = f"via alternative {i + 1} ({alt.action})"
            return DONE, f"{note}; {detail}" if detail else note
    return primary_status, primary_detail


def _all_hold(check: Check, step: Step, conditions: Sequence[Any]) -> bool:
    for cond in conditions:
        try:
            if not check(cond, step):
                return False
        except Exception as exc:
            _LOGGER.debug("plan: condition check raised (treated as unmet): %s", exc)
            return False
    return True


def _run_and_verify(step: Step, run_step: RunStep, check: Check,
                    verify_retries: int) -> Tuple[str, str]:
    attempts = max(1, verify_retries + 1)
    for attempt in range(1, attempts + 1):
        try:
            if not run_step(step):
                return FAILED, f"action returned falsy (attempt {attempt})"
        except Exception as exc:
            return FAILED, f"action raised: {exc}"
        if _all_hold(check, step, step.postconditions):
            return DONE, "" if attempt == 1 else f"verified after {attempt} attempts"
    return VERIFY_FAILED, f"postconditions unmet after {attempts} attempt(s)"


# ── async sibling (MCU Phase B / B-async) ────────────────────────────────────
# Real HA actuators ``await`` their service calls, but ``execute_plan`` is
# synchronous, so a path could not route an awaited action *through* the plan
# contract. ``aexecute_plan`` mirrors ``execute_plan`` exactly — same stages,
# statuses and semantics — with **awaitable** ``run_step`` / ``check`` callables.
# Still pure: no Home Assistant import and no I/O of its own; the side effects
# are the injected awaitables, so it stays deterministic and unit-testable.

ARunStep = Callable[[Step], Any]        # returns an awaitable → truthy on success
ACheck = Callable[[Any, Step], Any]      # returns an awaitable → bool


async def _aalways_true(_condition: Any, _step: Step) -> bool:
    return True


async def aexecute_plan(
    plan: Plan,
    *,
    run_step: ARunStep,
    check: Optional[ACheck] = None,
    completed: Optional[Set[str]] = None,
    verify_retries: int = 1,
) -> PlanReport:
    """Async form of :func:`execute_plan`: ``run_step`` and ``check`` are awaited.

    Same contract — skip already-done steps (by idempotency key), gate on
    preconditions, act, then verify postconditions (retrying the action
    ``verify_retries`` times), stopping at the first BLOCKED / FAILED /
    VERIFY_FAILED. ``completed`` is consulted and updated in place.
    """
    check = check or _aalways_true
    done_keys: Set[str] = completed if completed is not None else set()
    outcomes: List[StepOutcome] = []
    ok = True

    for step in plan.steps:
        if step.idempotency_key and step.idempotency_key in done_keys:
            outcomes.append(StepOutcome(step.id, step.action, SKIPPED,
                                        "idempotency_key already completed"))
            continue
        # Attempt the step, falling back through its alternatives (Phase U). A
        # step with no alternatives collapses to the old precondition→act→verify.
        status, detail = await _aattempt(step, run_step, check, verify_retries)
        if status != DONE and step.alternatives:
            status, detail = await _awith_alternatives(
                step, status, detail, run_step, check, verify_retries)
        outcomes.append(StepOutcome(step.id, step.action, status, detail))
        if status != DONE:
            ok = False
            break
        if step.idempotency_key:
            done_keys.add(step.idempotency_key)

    return PlanReport(plan_id=plan.id, ok=ok, outcomes=tuple(outcomes))


async def _aattempt(step: Step, run_step: ARunStep, check: ACheck,
                    verify_retries: int) -> Tuple[str, str]:
    """Async form of :func:`_attempt`: precondition-gate, act, then verify."""
    if not await _aall_hold(check, step, step.preconditions):
        return BLOCKED, "precondition not met"
    return await _arun_and_verify(step, run_step, check, verify_retries)


async def _awith_alternatives(step: Step, primary_status: str, primary_detail: str,
                              run_step: ARunStep, check: ACheck,
                              verify_retries: int) -> Tuple[str, str]:
    """Async form of :func:`_with_alternatives`."""
    for i, alt in enumerate(step.alternatives):
        status, detail = await _aattempt(alt, run_step, check, verify_retries)
        if status == DONE:
            note = f"via alternative {i + 1} ({alt.action})"
            return DONE, f"{note}; {detail}" if detail else note
    return primary_status, primary_detail


async def _aall_hold(check: ACheck, step: Step, conditions: Sequence[Any]) -> bool:
    for cond in conditions:
        try:
            if not await check(cond, step):
                return False
        except Exception as exc:
            _LOGGER.debug("plan: async condition check raised (treated as unmet): %s", exc)
            return False
    return True


async def _arun_and_verify(step: Step, run_step: ARunStep, check: ACheck,
                           verify_retries: int) -> Tuple[str, str]:
    attempts = max(1, verify_retries + 1)
    for attempt in range(1, attempts + 1):
        try:
            if not await run_step(step):
                return FAILED, f"action returned falsy (attempt {attempt})"
        except Exception as exc:
            return FAILED, f"action raised: {exc}"
        if await _aall_hold(check, step, step.postconditions):
            return DONE, "" if attempt == 1 else f"verified after {attempt} attempts"
    return VERIFY_FAILED, f"postconditions unmet after {attempts} attempt(s)"


# ── compensation (Phase U) ────────────────────────────────────────────────────
# A pure derivation, not an engine: the executor never auto-rolls-back. Given a
# report, this builds the undo plan from the compensations of the steps that
# actually took hold (DONE), newest-first, for a caller to run — or not — through
# the same authority + verify gates. A step DONE via one of its *alternatives*
# still compensates with its declared ``compensation`` (the step's effect took
# hold however it was achieved); a DONE step with no ``compensation`` is skipped.


def plan_compensation(report: PlanReport, plan: Plan, *,
                      correlation_id: Optional[str] = None) -> Plan:
    """Derive the rollback plan for the steps of ``plan`` that completed.

    Returns a new :class:`Plan` whose steps are the ``compensation`` of each
    step that reached DONE in ``report``, in reverse order of completion. Pure
    and **opt-in** — it builds the undo plan but never runs it; a caller that
    wants to unwind a partially-applied plan routes the result back through
    :func:`execute_plan` / :func:`aexecute_plan`. Steps without a declared
    ``compensation`` (or that never completed) contribute nothing.
    """
    by_id: Dict[str, Step] = {s.id: s for s in plan.steps}
    undo: List[Step] = []
    for outcome in report.outcomes:
        if outcome.status != DONE:
            continue
        step = by_id.get(outcome.step_id)
        if step is not None and step.compensation is not None:
            undo.append(step.compensation)
    undo.reverse()
    return Plan(
        goal=f"compensate: {plan.goal}",
        steps=tuple(undo),
        correlation_id=correlation_id if correlation_id is not None
        else plan.correlation_id,
    )
