"""Tests for the plan executor/verifier (kernel Phase 5). Pure, injected callables."""
import pytest


@pytest.fixture
def P(load):
    return load("kernel.plan")


def _plan(P, *steps, goal="g"):
    return P.Plan(goal=goal, steps=tuple(steps))


def test_happy_path_runs_all_and_verifies(P):
    ran = []
    plan = _plan(P,
                 P.Step(action="light.turn_on", postconditions=("is_on",)),
                 P.Step(action="announce"))
    rep = P.execute_plan(plan, run_step=lambda s: ran.append(s.action) or True,
                         check=lambda c, s: True)
    assert rep.ok
    assert [o.status for o in rep.outcomes] == [P.DONE, P.DONE]
    assert ran == ["light.turn_on", "announce"]


def test_precondition_blocks_and_stops(P):
    ran = []
    plan = _plan(P,
                 P.Step(action="lock.lock", preconditions=("door_closed",)),
                 P.Step(action="announce"))
    rep = P.execute_plan(
        plan,
        run_step=lambda s: ran.append(s.action) or True,
        check=lambda c, s: c != "door_closed",   # the precondition fails
    )
    assert not rep.ok
    assert rep.outcomes[0].status == P.BLOCKED
    assert len(rep.outcomes) == 1 and ran == []   # stopped; nothing ran


def test_action_failure_stops(P):
    plan = _plan(P, P.Step(action="cover.open"), P.Step(action="announce"))
    rep = P.execute_plan(plan, run_step=lambda s: False)   # action fails
    assert not rep.ok and rep.outcomes[0].status == P.FAILED
    assert len(rep.outcomes) == 1


def test_postcondition_retries_then_succeeds(P):
    calls = {"n": 0}

    def run(step):
        calls["n"] += 1
        return True

    # Verify fails on the first attempt, holds on the second.
    def check(cond, step):
        return calls["n"] >= 2

    plan = _plan(P, P.Step(action="light.turn_on", postconditions=("is_on",)))
    rep = P.execute_plan(plan, run_step=run, check=check, verify_retries=1)
    assert rep.ok and rep.outcomes[0].status == P.DONE
    assert calls["n"] == 2   # acted twice (initial + one retry)


def test_postcondition_never_holds_is_verify_failed(P):
    plan = _plan(P, P.Step(action="light.turn_on", postconditions=("is_on",)))
    rep = P.execute_plan(plan, run_step=lambda s: True, check=lambda c, s: False,
                         verify_retries=1)
    assert not rep.ok and rep.outcomes[0].status == P.VERIFY_FAILED


def test_idempotency_skips_completed_steps(P):
    ran = []
    plan = _plan(P,
                 P.Step(action="lock.lock", idempotency_key="lock-front"),
                 P.Step(action="announce"))
    completed = {"lock-front"}
    rep = P.execute_plan(plan, run_step=lambda s: ran.append(s.action) or True,
                         completed=completed)
    assert rep.ok
    assert rep.outcomes[0].status == P.SKIPPED
    assert ran == ["announce"]   # the locked step was skipped


def test_idempotency_key_recorded_after_success(P):
    completed = set()
    plan = _plan(P, P.Step(action="lock.lock", idempotency_key="lock-front"))
    P.execute_plan(plan, run_step=lambda s: True, completed=completed)
    assert "lock-front" in completed   # now recorded → a re-run would skip


def test_condition_check_raising_counts_as_unmet(P):
    def check(cond, step):
        raise RuntimeError("state read failed")
    plan = _plan(P, P.Step(action="x", preconditions=("c",)))
    rep = P.execute_plan(plan, run_step=lambda s: True, check=check)
    assert not rep.ok and rep.outcomes[0].status == P.BLOCKED


def test_report_to_dict_is_serialisable(P):
    plan = _plan(P, P.Step(action="announce"))
    rep = P.execute_plan(plan, run_step=lambda s: True)
    d = rep.to_dict()
    assert d["ok"] is True and d["plan_id"] == plan.id
    assert d["outcomes"][0]["action"] == "announce"


# ── async sibling: aexecute_plan (MCU Phase B / B-async) ─────────────────────

async def test_async_happy_path(P):
    ran = []
    async def run(s):
        ran.append(s.action)
        return True
    async def chk(c, s):
        return True
    plan = _plan(P,
                 P.Step(action="light.turn_on", postconditions=("is_on",)),
                 P.Step(action="announce"))
    rep = await P.aexecute_plan(plan, run_step=run, check=chk)
    assert rep.ok
    assert [o.status for o in rep.outcomes] == [P.DONE, P.DONE]
    assert ran == ["light.turn_on", "announce"]


async def test_async_precondition_blocks_and_stops(P):
    ran = []
    async def run(s):
        ran.append(s.action)
        return True
    async def chk(c, s):
        return c != "door_closed"   # precondition not met
    plan = _plan(P,
                 P.Step(action="lock.lock", preconditions=("door_closed",)),
                 P.Step(action="announce"))
    rep = await P.aexecute_plan(plan, run_step=run, check=chk)
    assert not rep.ok
    assert rep.outcomes[0].status == P.BLOCKED
    assert ran == []   # action never ran, and we stopped


async def test_async_idempotency_skip(P):
    ran = []
    async def run(s):
        ran.append(s.action)
        return True
    plan = _plan(P, P.Step(action="light.turn_on", idempotency_key="k1"))
    done = {"k1"}
    rep = await P.aexecute_plan(plan, run_step=run, completed=done)
    assert rep.ok and rep.outcomes[0].status == P.SKIPPED and ran == []


async def test_async_verify_retry_then_ok(P):
    calls = {"run": 0, "chk": 0}
    async def run(s):
        calls["run"] += 1
        return True
    async def chk(c, s):
        calls["chk"] += 1
        return calls["run"] >= 2   # postcondition holds only on the 2nd attempt
    plan = _plan(P, P.Step(action="cover.close", postconditions=("closed",)))
    rep = await P.aexecute_plan(plan, run_step=run, check=chk, verify_retries=1)
    assert rep.ok and rep.outcomes[0].status == P.DONE
    assert calls["run"] == 2   # acted twice (initial + one retry)


async def test_async_verify_failed(P):
    async def run(s):
        return True
    async def chk(c, s):
        return False   # never verifies
    plan = _plan(P, P.Step(action="cover.close", postconditions=("closed",)))
    rep = await P.aexecute_plan(plan, run_step=run, check=chk, verify_retries=1)
    assert not rep.ok and rep.outcomes[0].status == P.VERIFY_FAILED


async def test_async_action_raises_is_failed(P):
    async def run(s):
        raise RuntimeError("boom")
    plan = _plan(P, P.Step(action="light.turn_on"))
    rep = await P.aexecute_plan(plan, run_step=run)
    assert not rep.ok and rep.outcomes[0].status == P.FAILED
    assert "boom" in rep.outcomes[0].detail


async def test_async_default_check_is_always_true(P):
    async def run(s):
        return True
    plan = _plan(P, P.Step(action="scene.turn_on", postconditions=("x",)))
    rep = await P.aexecute_plan(plan, run_step=run)   # no check → default
    assert rep.ok and rep.outcomes[0].status == P.DONE
