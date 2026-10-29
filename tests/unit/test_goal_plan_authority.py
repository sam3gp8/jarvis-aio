"""MCU Phase H (H4b) — the kernel Plan is AUTHORITATIVE over goal step state.

The journal is now the source of truth for a goal's per-step status: writes
commit to it first and reads overlay it onto the goal store, which is kept as a
current mirror and durable fallback. Reversible via `goals_kernel_authoritative`.
These tests prove the authority semantics and — critically — that goal state is
never lost when the journal is absent.
"""
import json

import pytest


@pytest.fixture
def goals(load, tmp_path, monkeypatch):
    mod = load("goals")
    monkeypatch.setattr(mod, "DB_PATH", str(tmp_path / "patterns.db"))
    mod._journal = None
    mod._PLAN_PARITY = {"agree": 0, "disagree": 0}
    # Per-test isolated journal (the path is otherwise process-global).
    J = load("kernel.journal")
    jr = J.ExecutionJournal(str(tmp_path / "journal.db"))
    monkeypatch.setattr(mod, "_journal", jr)
    # Deterministic: H4b on unless a test overrides (default is on anyway).
    monkeypatch.setattr(mod, "_kernel_authoritative", lambda: True)
    return mod


def _make(goals, tmp_path, steps=("water", "mail"), name="goals.db"):
    db = str(tmp_path / name)
    res = goals.create("Tidy", "a tidy home", steps=list(steps), db_path=db)
    return res["id"], db


def test_update_commits_to_the_journal_first(goals, tmp_path):
    gid, db = _make(goals, tmp_path)
    goals.update(gid, step_updates=[{"n": 1, "status": "done"}], db_path=db)
    pid = goals._goal_plan_id(gid)
    jstatus = {r.step_id: r.status for r in goals._journal.steps(pid)}
    assert jstatus[goals._goal_step_id(gid, 1)] == "done"
    assert jstatus[goals._goal_step_id(gid, 2)] == "pending"


def test_journal_wins_when_the_store_diverges(goals, tmp_path):
    # The authority flip: if the store falls behind the journal, a read reflects
    # the journal (the source of truth), not the stale store row.
    gid, db = _make(goals, tmp_path)
    goals.update(gid, step_updates=[{"n": 1, "status": "done"}], db_path=db)
    # Force the store back to pending directly (bypassing update) — a store that
    # fell out of sync with the authoritative journal.
    g = goals.get(gid, db_path=db)
    g["steps"][0]["status"] = "pending"
    with goals._connect(db) as conn:
        conn.execute("UPDATE goals SET steps=? WHERE id=?",
                     (json.dumps(g["steps"]), int(gid)))
    # Read: the journal overlays → step 1 is authoritative "done" again.
    assert goals.get(gid, db_path=db)["steps"][0]["status"] == "done"


def test_store_is_the_fallback_when_journal_absent(goals, tmp_path):
    # No path to losing goal state: if the journal has no rows for the goal
    # (journal.db lost/reset), the store mirror stands.
    gid, db = _make(goals, tmp_path)
    goals.update(gid, step_updates=[{"n": 1, "status": "done"}], db_path=db)
    goals._journal.forget_plan(goals._goal_plan_id(gid))   # simulate lost journal
    assert goals._journal.steps(goals._goal_plan_id(gid)) == []
    g = goals.get(gid, db_path=db)
    assert g["steps"][0]["status"] == "done"               # recovered from store


def test_flag_off_reverts_to_store_authority(goals, tmp_path, monkeypatch):
    monkeypatch.setattr(goals, "_kernel_authoritative", lambda: False)
    gid, db = _make(goals, tmp_path)
    goals.update(gid, step_updates=[{"n": 1, "status": "done"}], db_path=db)
    # Make the journal disagree; with the flag off, reads must NOT overlay it.
    goals._journal.finish_step(goals._goal_plan_id(gid),
                               goals._goal_step_id(gid, 1), "failed")
    assert goals.get(gid, db_path=db)["steps"][0]["status"] == "done"


def test_create_forgets_stale_journal_rows_for_a_reused_id(goals, tmp_path):
    gid, db = _make(goals, tmp_path, steps=("a", "b"))
    goals.update(gid, step_updates=[{"n": 1, "status": "done"}], db_path=db)
    pid = goals._goal_plan_id(gid)
    assert any(r.status == "done" for r in goals._journal.steps(pid))
    # A fresh store hands out the SAME autoincrement id → create must clear the
    # stale journal rows so the new goal can't inherit the old "done".
    res = goals.create("New", "new outcome", steps=["x", "y"],
                       db_path=str(tmp_path / "goals2.db"))
    assert res["id"] == gid
    rows = goals._journal.steps(pid)
    assert rows and all(r.status == "pending" for r in rows)


def test_close_skips_abandoned_steps_in_store_and_journal(goals, tmp_path):
    gid, db = _make(goals, tmp_path, steps=("a", "b"))
    goals.update(gid, step_updates=[{"n": 1, "status": "done"}],
                 status="done", db_path=db)
    g = goals.get(gid, db_path=db)
    assert g["status"] == "done"
    assert g["steps"][0]["status"] == "done"
    assert g["steps"][1]["status"] == "skipped"            # abandoned → skipped
    jstatus = {r.step_id: r.status for r in goals._journal.steps(goals._goal_plan_id(gid))}
    assert jstatus[goals._goal_step_id(gid, 2)] == "skipped"   # journal agrees


def test_cancel_skips_open_steps_in_store_and_journal(goals, tmp_path):
    gid, db = _make(goals, tmp_path, steps=("a", "b"))
    assert goals.cancel(gid, db_path=db) is True
    g = goals.get(gid, db_path=db)
    assert g["status"] == "cancelled"
    assert [s["status"] for s in g["steps"]] == ["skipped", "skipped"]
    jstatus = [r.status for r in goals._journal.steps(goals._goal_plan_id(gid))]
    assert jstatus == ["skipped", "skipped"]


def test_parity_agrees_under_authority(goals, tmp_path):
    # With the journal authoritative and the store mirrored, parity is a live
    # invariant check — it should always agree.
    gid, db = _make(goals, tmp_path)
    goals.update(gid, step_updates=[{"n": 1, "status": "done"}], db_path=db)
    assert goals._plan_parity(goals.get(gid, db_path=db)) is True


def test_kernel_authoritative_default_on_and_reads_the_flag(load, monkeypatch):
    # The real helper (not the fixture's stub): default on, flag flips it off.
    mod = load("goals")
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get", lambda key, default=None: default)
    assert mod._kernel_authoritative() is True
    monkeypatch.setattr(jc, "get", lambda key, default=None:
                        (False if key == "goals_kernel_authoritative" else default))
    assert mod._kernel_authoritative() is False
