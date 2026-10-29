"""Tests for the execution journal + crash recovery (kernel hardening H4).

Real on-disk SQLite via tmp_path (persistence seam needs a file, not :memory:).
"""
from types import SimpleNamespace

import pytest


@pytest.fixture
def jmod(load):
    return load("kernel.journal")


def _step(sid, action="light.turn_on", key=None):
    return SimpleNamespace(id=sid, action=action, idempotency_key=key)


def _plan(pid="plan_1", steps=(), corr=None):
    return SimpleNamespace(id=pid, steps=tuple(steps), correlation_id=corr)


def _journal(jmod, tmp_path, **kw):
    return jmod.ExecutionJournal(str(tmp_path / "journal.db"), **kw)


def test_record_plan_writes_pending_steps(jmod, tmp_path):
    j = _journal(jmod, tmp_path)
    j.record_plan(_plan(steps=[_step("s1"), _step("s2")], corr="c1"))
    rows = j.steps("plan_1")
    assert [r.step_id for r in rows] == ["s1", "s2"]
    assert all(r.status == jmod.PENDING for r in rows)
    assert rows[0].correlation_id == "c1"
    assert rows[0].seq == 0 and rows[1].seq == 1


def test_record_plan_is_idempotent(jmod, tmp_path):
    j = _journal(jmod, tmp_path)
    p = _plan(steps=[_step("s1")])
    j.record_plan(p)
    j.start_step("plan_1", "s1")
    # Re-recording must not clobber the step's advanced status.
    j.record_plan(p)
    assert j.steps("plan_1")[0].status == jmod.RUNNING


def test_forget_plan_clears_rows_so_a_reseed_starts_fresh(jmod, tmp_path):
    # record_plan is INSERT-OR-IGNORE, so a reused deterministic plan_id would
    # otherwise inherit prior statuses. forget_plan clears them first.
    j = _journal(jmod, tmp_path)
    p = _plan(steps=[_step("s1"), _step("s2")])
    j.record_plan(p)
    j.finish_step("plan_1", "s1", jmod.DONE, "ok")
    assert j.steps("plan_1")[0].status == jmod.DONE
    # Without forgetting, a re-record keeps the DONE (idempotent-ignore).
    removed = j.forget_plan("plan_1")
    assert removed == 2
    assert j.steps("plan_1") == []
    # A fresh record now seeds clean PENDING rows, not the stale DONE.
    j.record_plan(p)
    rows = j.steps("plan_1")
    assert [r.step_id for r in rows] == ["s1", "s2"]
    assert all(r.status == jmod.PENDING for r in rows)


def test_forget_plan_absent_is_zero(jmod, tmp_path):
    j = _journal(jmod, tmp_path)
    assert j.forget_plan("nope") == 0


def test_lifecycle_start_then_finish(jmod, tmp_path):
    j = _journal(jmod, tmp_path)
    j.record_plan(_plan(steps=[_step("s1")]))
    j.start_step("plan_1", "s1")
    assert j.in_flight()[0].step_id == "s1"
    j.finish_step("plan_1", "s1", jmod.DONE, "ok")
    assert j.in_flight() == []
    assert j.steps("plan_1")[0].status == jmod.DONE
    assert j.plan_complete("plan_1")


def test_pending_excludes_terminal(jmod, tmp_path):
    j = _journal(jmod, tmp_path)
    j.record_plan(_plan(steps=[_step("s1"), _step("s2"), _step("s3")]))
    j.finish_step("plan_1", "s1", jmod.DONE)
    j.finish_step("plan_1", "s2", jmod.SKIPPED)
    pend = j.pending("plan_1")
    assert [r.step_id for r in pend] == ["s3"]
    assert not j.plan_complete("plan_1")


def test_in_flight_survives_new_journal_instance(jmod, tmp_path):
    # Simulate a crash: one step left RUNNING, then a fresh process opens the DB.
    db = str(tmp_path / "journal.db")
    j1 = jmod.ExecutionJournal(db)
    j1.record_plan(_plan(steps=[_step("s1"), _step("s2")]))
    j1.finish_step("plan_1", "s1", jmod.DONE)
    j1.start_step("plan_1", "s2")  # <-- "crash" here
    del j1

    j2 = jmod.ExecutionJournal(db)
    stuck = j2.in_flight()
    assert [r.step_id for r in stuck] == ["s2"]


def test_recover_verified_marks_done(jmod, tmp_path):
    j = _journal(jmod, tmp_path)
    j.record_plan(_plan(steps=[_step("s1")]))
    j.start_step("plan_1", "s1")
    rep = jmod.recover(j, verify=lambda step: True)
    assert rep.checked == 1 and rep.verified == 1 and rep.needs_replay == 0
    assert j.steps("plan_1")[0].status == jmod.DONE


def test_recover_unverified_without_act_needs_replay(jmod, tmp_path):
    j = _journal(jmod, tmp_path)
    j.record_plan(_plan(steps=[_step("s1")]))
    j.start_step("plan_1", "s1")
    rep = jmod.recover(j, verify=lambda step: False)
    assert rep.needs_replay == 1 and rep.verified == 0
    assert j.steps("plan_1")[0].status == jmod.NEEDS_REPLAY


def test_recover_unverified_with_act_replays(jmod, tmp_path):
    j = _journal(jmod, tmp_path)
    j.record_plan(_plan(steps=[_step("s1")]))
    j.start_step("plan_1", "s1")
    calls = []
    rep = jmod.recover(j, verify=lambda step: False,
                       act=lambda step: calls.append(step.step_id) or True)
    assert calls == ["s1"]
    assert rep.replayed == 1 and rep.verified == 1
    assert j.steps("plan_1")[0].status == jmod.DONE


def test_recover_act_failure_is_needs_replay(jmod, tmp_path):
    j = _journal(jmod, tmp_path)
    j.record_plan(_plan(steps=[_step("s1")]))
    j.start_step("plan_1", "s1")
    rep = jmod.recover(j, verify=lambda step: False, act=lambda step: False)
    assert rep.needs_replay == 1 and rep.replayed == 0
    assert j.steps("plan_1")[0].status == jmod.NEEDS_REPLAY


def test_recover_verify_raising_is_not_fatal(jmod, tmp_path):
    j = _journal(jmod, tmp_path)
    j.record_plan(_plan(steps=[_step("s1")]))
    j.start_step("plan_1", "s1")

    def boom(step):
        raise RuntimeError("sensor offline")

    rep = jmod.recover(j, verify=boom)
    assert rep.needs_replay == 1
    assert j.steps("plan_1")[0].status == jmod.NEEDS_REPLAY


def test_recover_noop_when_nothing_in_flight(jmod, tmp_path):
    j = _journal(jmod, tmp_path)
    j.record_plan(_plan(steps=[_step("s1")]))
    j.finish_step("plan_1", "s1", jmod.DONE)
    rep = jmod.recover(j, verify=lambda step: True)
    assert rep.to_dict() == {"checked": 0, "verified": 0,
                             "needs_replay": 0, "replayed": 0}


def test_injected_clock_is_used(jmod, tmp_path):
    ticks = iter([100.0, 200.0, 300.0])
    j = _journal(jmod, tmp_path, now=lambda: next(ticks))
    j.record_plan(_plan(steps=[_step("s1")]))   # created_ts/updated_ts = 100
    j.start_step("plan_1", "s1")                 # updated_ts = 200
    row = j.steps("plan_1")[0]
    assert row.created_ts == 100.0 and row.updated_ts == 200.0
