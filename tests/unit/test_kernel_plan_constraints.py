"""Phase U — constraint-aware planning (alternatives + compensation).

Pure, injected callables, same harness as test_kernel_plan.py. These assert the
two additive extensions and — crucially — that a step declaring neither behaves
exactly as the linear executor always has (covered by test_kernel_plan.py; here
we pin the new behaviour and its boundaries).
"""
import pytest


@pytest.fixture
def P(load):
    return load("kernel.plan")


def _plan(P, *steps, goal="g", correlation_id=None):
    return P.Plan(goal=goal, steps=tuple(steps), correlation_id=correlation_id)


# ── alternatives ─────────────────────────────────────────────────────────────

def test_alternative_used_when_primary_action_fails(P):
    ran = []

    def run(step):
        ran.append(step.action)
        return step.action != "light.turn_on"   # primary fails, alt succeeds

    plan = _plan(P, P.Step(
        action="light.turn_on",
        alternatives=(P.Step(action="scene.living_bright"),),
    ))
    rep = P.execute_plan(plan, run_step=run)
    assert rep.ok
    assert rep.outcomes[0].status == P.DONE
    assert "via alternative 1 (scene.living_bright)" in rep.outcomes[0].detail
    assert ran == ["light.turn_on", "scene.living_bright"]


def test_alternative_tried_in_order_first_success_wins(P):
    def run(step):
        return step.action == "group.overhead"   # only the 2nd alternative works

    plan = _plan(P, P.Step(
        action="light.turn_on",
        alternatives=(P.Step(action="scene.living_bright"),
                      P.Step(action="group.overhead"),
                      P.Step(action="never.reached")),
    ))
    rep = P.execute_plan(plan, run_step=run)
    assert rep.ok and rep.outcomes[0].status == P.DONE
    assert "via alternative 2 (group.overhead)" in rep.outcomes[0].detail


def test_all_alternatives_fail_reports_primary_failure(P):
    plan = _plan(P, P.Step(
        action="light.turn_on",
        alternatives=(P.Step(action="scene.x"), P.Step(action="group.y")),
    ), P.Step(action="announce"))
    rep = P.execute_plan(plan, run_step=lambda s: False)   # nothing succeeds
    assert not rep.ok
    # the step reports the PRIMARY's own failure, not an alternative's
    assert rep.outcomes[0].status == P.FAILED
    assert rep.outcomes[0].action == "light.turn_on"
    assert len(rep.outcomes) == 1   # stopped at the failed step


def test_alternative_covers_a_blocked_primary_precondition(P):
    # primary precondition unmet; the alternative has no precondition and runs
    plan = _plan(P, P.Step(
        action="lock.lock",
        preconditions=("door_closed",),
        alternatives=(P.Step(action="notify.cant_lock"),),
    ))
    rep = P.execute_plan(plan, run_step=lambda s: True,
                         check=lambda c, s: c != "door_closed")
    assert rep.ok and rep.outcomes[0].status == P.DONE
    assert "via alternative 1 (notify.cant_lock)" in rep.outcomes[0].detail


def test_alternative_with_own_unmet_precondition_is_skipped(P):
    def check(cond, step):
        return cond != "fail"   # both the primary and alt-1 preconditions fail

    plan = _plan(P, P.Step(
        action="primary",
        preconditions=("fail",),
        alternatives=(P.Step(action="alt1", preconditions=("fail",)),
                      P.Step(action="alt2")),   # alt2 has no precondition → runs
    ))
    rep = P.execute_plan(plan, run_step=lambda s: True, check=check)
    assert rep.ok and rep.outcomes[0].status == P.DONE
    assert "via alternative 2 (alt2)" in rep.outcomes[0].detail


def test_idempotency_key_recorded_when_satisfied_via_alternative(P):
    completed = set()
    plan = _plan(P, P.Step(
        action="lock.lock",
        idempotency_key="lock-front",
        alternatives=(P.Step(action="lock.lock_backup"),),
    ))
    P.execute_plan(plan, run_step=lambda s: s.action == "lock.lock_backup",
                   completed=completed)
    assert "lock-front" in completed   # the step's effect took hold → recorded


def test_alternative_verify_failed_falls_through_then_succeeds(P):
    # primary verifies-fail; alternative acts+verifies clean
    def check(cond, step):
        return step.action != "primary"   # only the primary's postcond never holds

    plan = _plan(P, P.Step(
        action="primary",
        postconditions=("held",),
        alternatives=(P.Step(action="alt", postconditions=("held",)),),
    ))
    rep = P.execute_plan(plan, run_step=lambda s: True, check=check,
                         verify_retries=0)
    assert rep.ok and rep.outcomes[0].status == P.DONE
    assert "via alternative 1 (alt)" in rep.outcomes[0].detail


# ── compensation (pure derivation; executor never auto-runs it) ──────────────

def test_executor_does_not_run_compensation(P):
    ran = []
    plan = _plan(P, P.Step(
        action="alarm.arm_away",
        compensation=P.Step(action="alarm.disarm"),
    ))
    rep = P.execute_plan(plan, run_step=lambda s: ran.append(s.action) or True)
    assert rep.ok
    assert ran == ["alarm.arm_away"]   # the compensation action was NOT executed


def test_plan_compensation_builds_reverse_undo_of_done_steps(P):
    s1 = P.Step(action="alarm.arm_away", compensation=P.Step(action="alarm.disarm"))
    s2 = P.Step(action="lock.lock", compensation=P.Step(action="lock.unlock"))
    s3 = P.Step(action="cover.close")   # fails, never DONE
    plan = _plan(P, s1, s2, s3, correlation_id="corr-1")

    def run(step):
        return step.action != "cover.close"   # s1, s2 DONE; s3 FAILED

    rep = P.execute_plan(plan, run_step=run)
    assert not rep.ok   # stopped at s3

    comp = P.plan_compensation(rep, plan)
    # only s1 + s2 completed and have compensations, newest-first → unlock, disarm
    assert [s.action for s in comp.steps] == ["lock.unlock", "alarm.disarm"]
    assert comp.goal == "compensate: g"
    assert comp.correlation_id == "corr-1"   # carried from the original plan


def test_plan_compensation_skips_done_steps_without_compensation(P):
    s1 = P.Step(action="alarm.arm_away", compensation=P.Step(action="alarm.disarm"))
    s2 = P.Step(action="announce")   # DONE but no compensation declared
    plan = _plan(P, s1, s2)
    rep = P.execute_plan(plan, run_step=lambda s: True)
    assert rep.ok

    comp = P.plan_compensation(rep, plan)
    assert [s.action for s in comp.steps] == ["alarm.disarm"]


def test_plan_compensation_empty_when_nothing_completed(P):
    plan = _plan(P, P.Step(action="x", compensation=P.Step(action="undo_x")))
    rep = P.execute_plan(plan, run_step=lambda s: False)   # x FAILED
    comp = P.plan_compensation(rep, plan)
    assert comp.steps == ()


def test_plan_compensation_override_correlation_id(P):
    s1 = P.Step(action="a", compensation=P.Step(action="undo_a"))
    plan = _plan(P, s1, correlation_id="orig")
    rep = P.execute_plan(plan, run_step=lambda s: True)
    comp = P.plan_compensation(rep, plan, correlation_id="rollback-99")
    assert comp.correlation_id == "rollback-99"


def test_compensation_derived_even_when_step_done_via_alternative(P):
    # the step's effect took hold (via its alternative) → it still compensates
    s1 = P.Step(
        action="primary",
        alternatives=(P.Step(action="alt"),),
        compensation=P.Step(action="undo_primary"),
    )
    plan = _plan(P, s1)
    rep = P.execute_plan(plan, run_step=lambda s: s.action == "alt")
    assert rep.ok and rep.outcomes[0].status == P.DONE

    comp = P.plan_compensation(rep, plan)
    assert [s.action for s in comp.steps] == ["undo_primary"]


# ── async alternatives ───────────────────────────────────────────────────────

async def test_async_alternative_used_when_primary_fails(P):
    ran = []

    async def run(step):
        ran.append(step.action)
        return step.action != "light.turn_on"

    plan = _plan(P, P.Step(
        action="light.turn_on",
        alternatives=(P.Step(action="scene.bright"),),
    ))
    rep = await P.aexecute_plan(plan, run_step=run)
    assert rep.ok and rep.outcomes[0].status == P.DONE
    assert "via alternative 1 (scene.bright)" in rep.outcomes[0].detail
    assert ran == ["light.turn_on", "scene.bright"]


async def test_async_all_alternatives_fail_reports_primary(P):
    async def run(step):
        return False

    plan = _plan(P, P.Step(
        action="primary",
        alternatives=(P.Step(action="alt1"), P.Step(action="alt2")),
    ))
    rep = await P.aexecute_plan(plan, run_step=run)
    assert not rep.ok
    assert rep.outcomes[0].status == P.FAILED
    assert rep.outcomes[0].action == "primary"
