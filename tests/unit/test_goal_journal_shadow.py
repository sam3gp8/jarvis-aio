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
    goals._shadow_plan("Tidy up", "a tidy home", [
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
    goals._shadow_plan("t", "o", [])        # no steps → nothing journaled
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
