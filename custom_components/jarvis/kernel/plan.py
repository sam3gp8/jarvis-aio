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
    """

    action: str
    params: dict = field(default_factory=dict)
    preconditions: Tuple[Any, ...] = ()
    postconditions: Tuple[Any, ...] = ()
    idempotency_key: Optional[str] = None
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

        # 2. Preconditions.
        if not _all_hold(check, step, step.preconditions):
            outcomes.append(StepOutcome(step.id, step.action, BLOCKED,
                                        "precondition not met"))
            ok = False
            break

        # 3. Act, then 4. verify postconditions — retrying the action if needed.
        status, detail = _run_and_verify(step, run_step, check, verify_retries)
        outcomes.append(StepOutcome(step.id, step.action, status, detail))
        if status != DONE:
            ok = False
            break
        if step.idempotency_key:
            done_keys.add(step.idempotency_key)

    return PlanReport(plan_id=plan.id, ok=ok, outcomes=tuple(outcomes))


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
        if not await _aall_hold(check, step, step.preconditions):
            outcomes.append(StepOutcome(step.id, step.action, BLOCKED,
                                        "precondition not met"))
            ok = False
            break
        status, detail = await _arun_and_verify(step, run_step, check, verify_retries)
        outcomes.append(StepOutcome(step.id, step.action, status, detail))
        if status != DONE:
            ok = False
            break
        if step.idempotency_key:
            done_keys.add(step.idempotency_key)

    return PlanReport(plan_id=plan.id, ok=ok, outcomes=tuple(outcomes))


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
