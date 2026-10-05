"""MCU Phase F (F2): a goal's shadow Plan is durably recorded in the execution
journal, so the goal → plan → step chain is reconstructable (agency recovery).

Shadow / log-only: the journal is written alongside goal creation but never
replayed in the live flow, and the goal store stays authoritative.
"""
import pytest


@pytest.fixture
def goals(load):
    mod = load("goals")
    mod._journal = None
    return mod


def test_shadow_plan_records_plan_into_journal(goals, monkeypatch):
    recorded = []

    class _FakeJournal:
        def record_plan(self, plan):
            recorded.append(plan)

    monkeypatch.setattr(goals, "_journal", _FakeJournal())
    goals._shadow_plan(1, "Tidy up", "a tidy home", [
        {"n": 1, "step": "water the plants", "status": "pending"},
        {"n": 2, "step": "check the mail", "status": "pending"},
    ])
    assert len(recorded) == 1
    plan = recorded[0]
    assert plan.goal == "Tidy up"
    assert [s.action for s in plan.steps] == ["water the plants", "check the mail"]


def test_shadow_plan_no_steps_does_not_journal(goals, monkeypatch):
    recorded = []

    class _FakeJournal:
        def record_plan(self, plan):
            recorded.append(plan)

    monkeypatch.setattr(goals, "_journal", _FakeJournal())
    goals._shadow_plan(1, "t", "o", [])        # no steps → nothing journaled
    assert recorded == []


def test_journal_roundtrip_reconstructs_the_chain(goals, load, tmp_path, monkeypatch):
    # With a real journal on a tmp db, the recorded plan's steps read back.
    J = load("kernel.journal")
    P = load("kernel.plan")
    journal = J.ExecutionJournal(str(tmp_path / "journal.db"))
    monkeypatch.setattr(goals, "_journal", journal)

    # Capture the plan id the shadow builds by spying the kernel Plan constructor
    # is overkill; instead record a known plan directly through the same path.
    plan = P.Plan(goal="Tidy", steps=(
        P.Step(action="water", params={"n": 1}),
        P.Step(action="mail", params={"n": 2}),
    ))
    journal.record_plan(plan)
    rows = journal.steps(plan.id)
    assert [r.action for r in rows] == ["water", "mail"]
    assert all(r.status == J.PENDING for r in rows)


def test_create_still_works_with_journal_wired(goals, tmp_path, monkeypatch):
    # Goal creation is behaviour-identical even though it now journals.
    monkeypatch.setattr(goals, "_journal", type("J", (), {
        "record_plan": lambda self, plan: None})())
    db = str(tmp_path / "patterns.db")
    res = goals.create("Tidy", "a tidy home", steps=["water", "mail"], db_path=db)
    assert "error" not in res and len(res["steps"]) == 2


# ── H4a: the full goal LIFECYCLE is recorded in the journal ──────────────────

def test_goal_lifecycle_lands_on_the_same_journal_rows(goals, load, tmp_path, monkeypatch):
    """create → step advance → close all address the SAME (plan_id, step_id)
    rows (deterministic ids), so the journal reconstructs what happened to the
    goal, not just that it was planned."""
    J = load("kernel.journal")
    journal = J.ExecutionJournal(str(tmp_path / "journal.db"))
    monkeypatch.setattr(goals, "_journal", journal)
    db = str(tmp_path / "goals.db")

    res = goals.create("Tidy", "a tidy home", steps=["water", "mail"], db_path=db)
    gid = res["id"]
    pid = goals._goal_plan_id(gid)
    # recorded at creation as pending
    rows = {r.step_id: r for r in journal.steps(pid)}
    assert set(rows) == {goals._goal_step_id(gid, 1), goals._goal_step_id(gid, 2)}
    assert all(r.status == J.PENDING for r in rows.values())

    # advance step 1 → done; it finishes on the SAME row (no duplicate)
    goals.update(gid, step_updates=[{"n": 1, "status": "done"}], db_path=db)
    rows = {r.step_id: r for r in journal.steps(pid)}
    assert len(rows) == 2
    assert rows[goals._goal_step_id(gid, 1)].status == "done"
    assert rows[goals._goal_step_id(gid, 2)].status == J.PENDING

    # close the goal → the still-open step 2 is journaled as skipped
    goals.update(gid, status="done", db_path=db)
    rows = {r.step_id: r for r in journal.steps(pid)}
    assert rows[goals._goal_step_id(gid, 1)].status == "done"
    assert rows[goals._goal_step_id(gid, 2)].status == "skipped"


def test_cancel_journals_terminal_state(goals, load, tmp_path, monkeypatch):
    J = load("kernel.journal")
    journal = J.ExecutionJournal(str(tmp_path / "journal.db"))
    monkeypatch.setattr(goals, "_journal", journal)
    db = str(tmp_path / "goals.db")
    res = goals.create("Tidy", "a tidy home", steps=["water"], db_path=db)
    gid = res["id"]
    assert goals.cancel(gid, db_path=db) is True
    rows = journal.steps(goals._goal_plan_id(gid))
    assert rows and rows[0].status == "skipped"
