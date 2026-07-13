"""Tests for the goal planner (v6.40.0) — persistent outcomes pursued across
time: lifecycle, the goal prompt, quiet-while-working engagement, closure
announcements, deadlines, and the safety valves."""
import sqlite3
import sys
import types
from datetime import datetime, timedelta

import pytest

if "homeassistant.helpers.llm" not in sys.modules:      # for the agent load
    _llm = types.ModuleType("homeassistant.helpers.llm")
    _llm.async_get_api = lambda *a, **k: None
    sys.modules["homeassistant.helpers.llm"] = _llm

NOW = datetime(2026, 7, 12, 9, 0, 0)


@pytest.fixture
def goals(load, tmp_path, monkeypatch):
    mod = load("goals")
    monkeypatch.setattr(mod, "DB_PATH", str(tmp_path / "patterns.db"))
    return mod


@pytest.fixture
def quiet_activity(load, monkeypatch):
    db = load("database")
    sink = []
    monkeypatch.setattr(db, "save_activity", lambda **kw: sink.append(kw))
    return sink


def _mk(goals, **kw):
    args = dict(title="Guest prep", outcome="house ready for guests",
                steps=["tidy living room", "stock fridge"], now=NOW)
    args.update(kw)
    return goals.create(args.pop("title"), args.pop("outcome"),
                        args.pop("steps"), **args)


# ── lifecycle ────────────────────────────────────────────────────────────────

def test_create_is_immediately_due_with_normalized_steps(goals):
    res = _mk(goals)
    assert res["id"] == 1
    assert res["steps"][0] == {"n": 1, "step": "tidy living room",
                               "status": "pending", "note": ""}
    assert [g["id"] for g in goals.due(now=NOW)] == [1]   # first engagement now


def test_guards(goals, monkeypatch):
    assert "error" in goals.create("t", "", [])
    monkeypatch.setattr(goals, "MAX_ACTIVE", 1)
    assert "id" in _mk(goals)
    assert "error" in _mk(goals, title="second")


def test_due_gating_and_update_mechanics(goals):
    gid = _mk(goals)["id"]
    goals.update(gid, step_updates=[{"n": 1, "status": "done", "note": "spotless"}],
                 next_check_minutes=60, progress_note="tidied", now=NOW)
    assert goals.due(now=NOW + timedelta(minutes=30)) == []      # pushed out
    g = goals.get(gid)
    assert g["steps"][0]["status"] == "done" and g["steps"][0]["note"] == "spotless"
    assert g["progress"][-1]["note"] == "tidied"
    assert [x["id"] for x in goals.due(now=NOW + timedelta(minutes=61))] == [gid]


def test_close_and_cancel(goals):
    gid = _mk(goals)["id"]
    goals.update(gid, status="done", result="All set, sir.", now=NOW)
    assert goals.get(gid)["status"] == "done"
    assert goals.cancel(gid) is False                # not active anymore
    gid2 = _mk(goals, title="other")["id"]
    assert goals.cancel(gid2) is True and goals.active() == []


def test_missing_db_graceful(goals):
    assert goals.get(1, db_path="/nope/p.db") is None
    assert goals.active(db_path="/nope/p.db") == []
    assert goals.cancel(1, db_path="/nope/p.db") is False
    assert goals.recent(db_path="/nope/p.db") == []


def test_recent_includes_all_statuses_newest_first(goals):
    gid1 = _mk(goals, title="first")["id"]
    goals.update(gid1, status="done", result="ok", now=NOW)
    gid2 = _mk(goals, title="second", now=NOW + timedelta(minutes=5))["id"]
    goals.update(gid2, status="failed", result="nope", now=NOW + timedelta(minutes=10))
    gid3 = _mk(goals, title="third", now=NOW + timedelta(minutes=20))["id"]  # stays active

    rows = goals.recent()
    ids = [r["id"] for r in rows]
    assert ids == [gid3, gid2, gid1]                    # newest updated_ts first
    statuses = {r["id"]: r["status"] for r in rows}
    assert statuses == {gid1: "done", gid2: "failed", gid3: "active"}


def test_recent_respects_limit(goals):
    for i in range(5):
        _mk(goals, title=f"g{i}", now=NOW + timedelta(minutes=i))
    assert len(goals.recent(limit=2)) == 2


# ── the goal prompt ──────────────────────────────────────────────────────────

def test_build_prompt_contents(goals):
    gid = _mk(goals, deadline_minutes=120)["id"]
    goals.update(gid, step_updates=[{"n": 1, "status": "done"}],
                 progress_note="started", now=NOW)
    g = goals.get(gid)
    p = goals.build_prompt(g)
    assert "OUTCOME to achieve: house ready for guests" in p
    assert "[x] 1. tidy living room" in p and "[ ] 2. stock fridge" in p
    assert "started" in p and "Deadline:" in p
    assert f"update_goal(goal_id={gid}" in p
    assert "DEADLINE HAS PASSED" in goals.build_prompt(g, deadline_passed=True)


# ── engagement (process_due) ─────────────────────────────────────────────────

async def test_active_goal_runs_quietly_and_rearms(goals, fake_hass, quiet_activity):
    gid = _mk(goals)["id"]
    seen = []
    async def runner(prompt, ctx):
        seen.append(prompt)
        goals.update(gid, progress_note="checked, nothing yet",
                     now=NOW, db_path=goals.DB_PATH)
        return "Nothing needs doing yet."
    actions = await goals.async_process_due(fake_hass, {}, runner, now=NOW,
                                            db_path=goals.DB_PATH)
    assert actions == []                              # quiet while working
    assert "OUTCOME to achieve" in seen[0]
    g = goals.get(gid)
    assert g["runs"] == 1
    assert g["next_check_ts"] == (NOW + timedelta(minutes=30)).isoformat(sep=" ")
    assert quiet_activity and quiet_activity[0]["category"] == "goal"


async def test_model_next_check_overrides_default(goals, fake_hass, quiet_activity):
    gid = _mk(goals)["id"]
    async def runner(prompt, ctx):
        goals.update(gid, next_check_minutes=120, now=NOW, db_path=goals.DB_PATH)
        return "Re-engaging in two hours."
    await goals.async_process_due(fake_hass, {}, runner, now=NOW,
                                  db_path=goals.DB_PATH)
    assert goals.get(gid)["next_check_ts"] == \
        (NOW + timedelta(minutes=120)).isoformat(sep=" ")


async def test_completion_announces_result(goals, fake_hass, quiet_activity):
    gid = _mk(goals)["id"]
    async def runner(prompt, ctx):
        goals.update(gid, status="done", result="The house is guest-ready, sir.",
                     now=NOW, db_path=goals.DB_PATH)
        return "done"
    actions = await goals.async_process_due(fake_hass, {}, runner, now=NOW,
                                            db_path=goals.DB_PATH)
    assert len(actions) == 1
    assert actions[0]["type"] == "goal" and actions[0]["urgency"] == "medium"
    assert "guest-ready" in actions[0]["message"]


async def test_deadline_passed_forces_closure(goals, fake_hass, quiet_activity):
    gid = _mk(goals, deadline_minutes=10)["id"]
    async def runner(prompt, ctx):
        assert "DEADLINE HAS PASSED" in prompt
        return "Ran out of time."                     # model leaves it active
    late = NOW + timedelta(minutes=30)
    actions = await goals.async_process_due(fake_hass, {}, runner, now=late,
                                            db_path=goals.DB_PATH)
    g = goals.get(gid)
    assert g["status"] == "failed"
    assert len(actions) == 1 and "Ran out of time" in actions[0]["message"]


async def test_runner_error_keeps_goal_active(goals, fake_hass, quiet_activity):
    gid = _mk(goals)["id"]
    async def runner(prompt, ctx):
        raise RuntimeError("provider down")
    actions = await goals.async_process_due(fake_hass, {}, runner, now=NOW,
                                            db_path=goals.DB_PATH)
    assert actions == []
    g = goals.get(gid)
    assert g["status"] == "active"                    # transient outage ≠ failure
    assert "run error: provider down" in g["progress"][-1]["note"]
    assert g["next_check_ts"] > NOW.isoformat(sep=" ")   # pre-armed


async def test_run_budget_force_fails(goals, fake_hass, quiet_activity):
    gid = _mk(goals)["id"]
    with sqlite3.connect(goals.DB_PATH) as c:
        c.execute("UPDATE goals SET runs=? WHERE id=?", (goals.MAX_RUNS, gid))
    async def runner(prompt, ctx):
        raise AssertionError("budget-exhausted goal must not run")
    actions = await goals.async_process_due(fake_hass, {}, runner, now=NOW,
                                            db_path=goals.DB_PATH)
    assert goals.get(gid)["status"] == "failed"
    assert len(actions) == 1 and "setting it aside" in actions[0]["message"]


# ── agent surface ────────────────────────────────────────────────────────────

def test_goal_tools_registered(load):
    agent = load("agent")
    names = {t["function"]["name"] for t in agent.JARVIS_TOOLS}
    assert {"create_goal", "update_goal", "manage_goals"} <= names
    for n in ("create_goal", "update_goal", "manage_goals"):
        assert n in agent._TOOL_MAP


async def test_exec_roundtrip(load, goals, fake_hass):
    agent = load("agent")
    out = await agent._exec_create_goal(fake_hass, {
        "title": "Warm den", "outcome": "den at 72F",
        "steps": ["set thermostat", "confirm temperature"]})
    assert "Goal #1 opened" in out and "confirm temperature" in out
    listing = await agent._exec_manage_goals(fake_hass, {"action": "list"})
    assert "Warm den" in listing and "0/2" in listing
    upd = await agent._exec_update_goal(fake_hass, {
        "goal_id": 1, "step_updates": [{"n": 1, "status": "done"}],
        "progress_note": "thermostat set"})
    assert "recorded" in upd
    status = await agent._exec_manage_goals(fake_hass,
                                            {"action": "status", "goal_id": 1})
    assert "[done] 1." in status and "thermostat set" in status
    assert "cancelled" in await agent._exec_manage_goals(
        fake_hass, {"action": "cancel", "goal_id": 1})
