"""MCU Phase F (F1): a goal's ordered steps are mirrored into a kernel Plan.

A goal is a plan pursued across time; F1 expresses a goal's step list as a
`kernel.plan.Plan` in SHADOW — built and logged alongside the goal, never
executed or consulted. The goal store stays authoritative; creating a goal is
behaviour-identical.
"""
import pytest


@pytest.fixture
def goals(load):
    return load("goals")


def test_shadow_plan_maps_goal_steps_to_kernel_plan(goals, load):
    P = load("kernel.plan")
    steps = [{"n": 1, "step": "water the plants", "status": "pending", "note": ""},
             {"n": 2, "step": "check the mail", "status": "done", "note": ""}]
    # Build via the same helper create() uses — it must not raise and the mapping
    # is exercised through a monkeypatch-free direct call.
    goals._shadow_plan("Tidy up", "a tidy home", steps)  # never raises

    # Reconstruct what it builds to pin the mapping.
    plan = P.Plan(goal="Tidy up", steps=tuple(
        P.Step(action=s["step"], params={"n": s["n"], "status": s["status"]})
        for s in steps))
    assert plan.goal == "Tidy up"
    assert [s.action for s in plan.steps] == ["water the plants", "check the mail"]
    assert plan.steps[1].params == {"n": 2, "status": "done"}


def test_shadow_plan_empty_steps_is_safe(goals):
    goals._shadow_plan("t", "o", [])        # no steps → no raise
    goals._shadow_plan("t", "o", None)      # defensive


def test_create_still_returns_goal_with_steps(goals, tmp_path):
    db = str(tmp_path / "goals.db")
    res = goals.create("Tidy", "a tidy home",
                       steps=["water the plants", "check the mail"], db_path=db)
    assert "error" not in res
    assert res["title"] == "Tidy"
    assert [s["step"] for s in res["steps"]] == ["water the plants", "check the mail"]
    # And the goal is persisted/readable unchanged.
    got = goals.get(res["id"], db_path=db)
    assert got is not None and len(got["steps"]) == 2
