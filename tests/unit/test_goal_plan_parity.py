"""MCU Phase F/H — goals plan PARITY (9.38.0).

On each engagement the durable kernel Plan (built by `_shadow_plan` / F1-F2 and
kept in sync with the goal store by `_journal_goal_progress` / H4a) is CONSULTED
against the authoritative goal store, and whether they agree is recorded
(log-only). This is the rung from shadow (built, never consulted) to parity
(consulted in the live flow). The goal store stays authoritative; nothing here
drives goal state or execution.
"""
import pytest


@pytest.fixture
def goals(load):
    mod = load("goals")
    mod._journal = None
    mod._PLAN_PARITY = {"agree": 0, "disagree": 0}
    return mod


@pytest.fixture
def journal(goals, load, tmp_path, monkeypatch):
    J = load("kernel.journal")
    jr = J.ExecutionJournal(str(tmp_path / "journal.db"))
    monkeypatch.setattr(goals, "_journal", jr)
    return jr


def _make_goal(goals, tmp_path):
    db = str(tmp_path / "goals.db")
    res = goals.create("Tidy", "a tidy home", steps=["water", "mail"], db_path=db)
    return res["id"], db


def test_parity_agrees_when_journal_matches_store(goals, journal, tmp_path):
    gid, db = _make_goal(goals, tmp_path)
    g = goals.get(gid, db_path=db)
    assert goals._plan_parity(g) is True
    assert goals._PLAN_PARITY["agree"] == 1
    # A real step advance updates BOTH the store and the journal (update → H4a),
    # so parity still agrees.
    goals.update(gid, step_updates=[{"n": 1, "status": "done"}], db_path=db)
    g = goals.get(gid, db_path=db)
    assert goals._plan_parity(g) is True


def test_parity_diverges_when_store_disagrees_with_journal(goals, journal, tmp_path):
    gid, db = _make_goal(goals, tmp_path)
    g = goals.get(gid, db_path=db)
    # Hand-diverge: the store view marks step 1 done, but no update() ran to
    # mirror it into the journal (still pending) — parity must catch the drift.
    g["steps"][0]["status"] = "done"
    assert goals._plan_parity(g) is False
    assert goals._PLAN_PARITY["disagree"] == 1


def test_parity_none_when_nothing_journaled(goals, journal):
    # A goal whose plan was never journaled → nothing to compare → None.
    g = {"id": 999, "steps": [{"n": 1, "step": "x", "status": "pending"}]}
    assert goals._plan_parity(g) is None


def test_parity_none_when_no_steps(goals, journal):
    assert goals._plan_parity({"id": 1, "steps": []}) is None


def test_parity_never_raises(goals):
    # Defensive: a malformed goal is swallowed (log-only, best-effort).
    assert goals._plan_parity({}) is None
    assert goals._plan_parity({"id": "x", "steps": [{"bad": True}]}) in (None, False)


def test_parity_is_log_only_does_not_touch_store(goals, journal, tmp_path):
    # Consulting parity must not mutate the goal store or its steps.
    gid, db = _make_goal(goals, tmp_path)
    before = goals.get(gid, db_path=db)
    goals._plan_parity(before)
    after = goals.get(gid, db_path=db)
    assert after["steps"] == before["steps"]
    assert after["status"] == before["status"]
