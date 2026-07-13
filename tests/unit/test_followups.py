"""Tests for self-scheduled follow-ups (v6.38.0) — the agent queuing work for
its future self, and the core tick executing it."""
from datetime import datetime, timedelta

import pytest

NOW = datetime(2026, 7, 12, 3, 0, 0)


@pytest.fixture
def fu(load, tmp_path, monkeypatch):
    mod = load("followups")
    monkeypatch.setattr(mod, "DB_PATH", str(tmp_path / "patterns.db"))
    return mod


def test_schedule_and_pending_roundtrip(fu):
    res = fu.schedule("check the garage closed", 5, now=NOW)
    assert res["id"] == 1
    assert res["due_ts"] == (NOW + timedelta(minutes=5)).isoformat(sep=" ")
    rows = fu.pending(now=NOW)
    assert len(rows) == 1 and rows[0]["instruction"] == "check the garage closed"


def test_due_only_gating(fu):
    fu.schedule("later", 30, now=NOW)
    fu.schedule("now-ish", 1, now=NOW)
    due = fu.pending(now=NOW + timedelta(minutes=2), due_only=True)
    assert [r["instruction"] for r in due] == ["now-ish"]


def test_bad_input_is_error_dict(fu):
    assert "error" in fu.schedule("", 5)
    assert "error" in fu.schedule("x", "soon")


def test_queue_full_guard(fu, monkeypatch):
    monkeypatch.setattr(fu, "MAX_OPEN", 2)
    assert "id" in fu.schedule("a", 5, now=NOW)
    assert "id" in fu.schedule("b", 5, now=NOW)
    assert "error" in fu.schedule("c", 5, now=NOW)


def test_cancel_only_pending(fu):
    rid = fu.schedule("a", 5, now=NOW)["id"]
    assert fu.cancel(rid) is True
    assert fu.cancel(rid) is False          # already cancelled
    assert fu.pending(now=NOW) == []


def test_missing_db_graceful(fu):
    assert fu.pending(db_path="/nope/patterns.db") == []
    assert fu.cancel(1, db_path="/nope/patterns.db") is False


async def test_process_due_runs_marks_and_returns_action(fu, load, fake_hass, monkeypatch):
    db = load("database")
    logged = []
    monkeypatch.setattr(db, "save_activity", lambda **kw: logged.append(kw))
    fu.schedule("verify the heat reached 72", 1, now=NOW)

    ran = []
    async def runner(instruction, context):
        ran.append((instruction, context))
        return "The living room is at 72 degrees, sir."

    actions = await fu.async_process_due(
        fake_hass, {}, runner, now=NOW + timedelta(minutes=2),
        db_path=fu.DB_PATH)
    assert ran and ran[0][0] == "verify the heat reached 72"
    assert len(actions) == 1
    assert actions[0]["type"] == "followup" and actions[0]["urgency"] == "medium"
    assert "72 degrees" in actions[0]["message"]
    row = [r for r in _all(fu)][0]
    assert row["status"] == "done" and "72 degrees" in row["result"]
    assert logged and logged[0]["category"] == "followup"


async def test_process_due_failure_marks_failed(fu, fake_hass):
    fu.schedule("boom", 1, now=NOW)

    async def runner(instruction, context):
        raise RuntimeError("provider down")

    actions = await fu.async_process_due(
        fake_hass, {}, runner, now=NOW + timedelta(minutes=2), db_path=fu.DB_PATH)
    assert actions == []
    row = _all(fu)[0]
    assert row["status"] == "failed" and "provider down" in row["result"]


async def test_process_due_without_runner_fails_safely(fu, fake_hass):
    fu.schedule("orphan", 1, now=NOW)
    actions = await fu.async_process_due(
        fake_hass, {}, None, now=NOW + timedelta(minutes=2), db_path=fu.DB_PATH)
    assert actions == []
    assert _all(fu)[0]["status"] == "failed"


def _all(fu):
    import sqlite3
    with sqlite3.connect(fu.DB_PATH) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM followups").fetchall()]
