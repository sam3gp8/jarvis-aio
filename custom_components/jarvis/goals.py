"""
JARVIS goal planner — outcomes pursued across time.

The third tier of agency. `execute_plan` runs many service calls *now*;
`schedule_followup` runs one instruction *later*; a **goal** is an *outcome*
JARVIS keeps working toward until it's done, failed, or called off:

    "Get the house ready for guests by Saturday 4pm"
    "Warm the living room to 72 and tell me when it's there"
    "Keep an eye on the basement humidity today; dehumidify if it climbs"

A goal carries its plan (steps with states), a progress log, a re-engagement
cadence, and an optional deadline. When one comes due, the cognitive core runs
it through the same headless agent brain the follow-ups use — full toolset:
check states, act (verify-after-act rides along), investigate — under a prompt
that mandates recording progress via the `update_goal` tool before finishing.

Noise policy (deliberate): goals work **quietly**. Mid-progress runs log to the
activity trail but say nothing out loud; JARVIS speaks when a goal *finishes* —
done or failed — through the normal routing, so quiet hours apply. "What are
you working on?" reviews them any time.

Safety valves: bounded active goals, a per-goal run budget, pre-armed
re-engagement (a crash can never hot-loop a goal), and deadlines that force
closure. Same construction discipline as followups.py: sqlite in patterns.db,
call-time db-path resolution, SYNC CRUD via executor, never raises.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

_LOGGER = logging.getLogger(__name__)

DB_PATH = "/config/jarvis/patterns.db"
MAX_ACTIVE = 10            # concurrent outcomes JARVIS may pursue
MAX_RUNS = 120             # per-goal engagement budget (runaway guard)
DEFAULT_INTERVAL_MIN = 30  # re-engage cadence when the model doesn't set one
MAX_DEADLINE_MIN = 30 * 24 * 60   # a month out, at most
PROGRESS_TAIL = 5          # progress entries shown in the goal prompt
STATUSES = ("active", "done", "failed", "cancelled")


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_ts TEXT NOT NULL,
            updated_ts TEXT NOT NULL,
            title TEXT NOT NULL,
            outcome TEXT NOT NULL,
            steps TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'active',
            progress TEXT NOT NULL DEFAULT '[]',
            next_check_ts TEXT NOT NULL,
            check_interval_min REAL NOT NULL DEFAULT 30,
            deadline_ts TEXT,
            runs INTEGER NOT NULL DEFAULT 0,
            last_result TEXT DEFAULT ''
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_goal_due ON goals(status, next_check_ts)")
    return conn


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now()


def _iso(d: datetime) -> str:
    return d.isoformat(sep=" ")


def _row_to_goal(r) -> dict:
    g = dict(r)
    for key in ("steps", "progress"):
        try:
            g[key] = json.loads(g.get(key) or "[]")
        except Exception:
            g[key] = []
    return g


# ── CRUD (sync — executor) ───────────────────────────────────────────────────

def create(title: str, outcome: str, steps: Optional[list] = None, *,
           check_interval_min: float = DEFAULT_INTERVAL_MIN,
           deadline_minutes: Optional[float] = None,
           now: Optional[datetime] = None,
           db_path: Optional[str] = None) -> dict:
    """Open a new goal. Returns the row, or an 'error' dict — never raises."""
    db_path = db_path or DB_PATH
    outcome = (outcome or "").strip()
    title = (title or outcome[:60] or "").strip()
    if not outcome:
        return {"error": "empty outcome"}
    try:
        interval = max(1.0, min(float(check_interval_min or DEFAULT_INTERVAL_MIN),
                                24 * 60.0))
    except (TypeError, ValueError):
        interval = DEFAULT_INTERVAL_MIN
    t = _now(now)
    deadline = None
    if deadline_minutes is not None:
        try:
            deadline = _iso(t + timedelta(
                minutes=max(1.0, min(float(deadline_minutes), MAX_DEADLINE_MIN))))
        except (TypeError, ValueError):
            deadline = None
    norm_steps = [{"n": i + 1, "step": str(s).strip(), "status": "pending",
                   "note": ""}
                  for i, s in enumerate(steps or []) if str(s).strip()]
    try:
        with _connect(db_path) as conn:
            open_n = conn.execute(
                "SELECT COUNT(*) FROM goals WHERE status='active'").fetchone()[0]
            if open_n >= MAX_ACTIVE:
                return {"error": f"goal list is full ({MAX_ACTIVE} active)"}
            cur = conn.execute(
                "INSERT INTO goals (created_ts, updated_ts, title, outcome, steps, "
                "next_check_ts, check_interval_min, deadline_ts) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (_iso(t), _iso(t), title, outcome, json.dumps(norm_steps),
                 _iso(t), interval, deadline))   # due immediately: first engagement
            return {"id": cur.lastrowid, "title": title, "outcome": outcome,
                    "steps": norm_steps, "next_check_ts": _iso(t),
                    "deadline_ts": deadline}
    except Exception as exc:
        _LOGGER.warning("goals.create failed: %s", exc)
        return {"error": str(exc)}


def get(goal_id: int, *, db_path: Optional[str] = None) -> Optional[dict]:
    db_path = db_path or DB_PATH
    try:
        with _connect(db_path) as conn:
            r = conn.execute("SELECT * FROM goals WHERE id=?",
                             (int(goal_id),)).fetchone()
            return _row_to_goal(r) if r else None
    except Exception:
        return None


def active(*, db_path: Optional[str] = None) -> list[dict]:
    db_path = db_path or DB_PATH
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE status='active' "
                "ORDER BY datetime(next_check_ts)").fetchall()
            return [_row_to_goal(r) for r in rows]
    except Exception:
        return []


def due(*, now: Optional[datetime] = None,
        db_path: Optional[str] = None) -> list[dict]:
    db_path = db_path or DB_PATH
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE status='active' AND "
                "datetime(next_check_ts) <= datetime(?) "
                "ORDER BY datetime(next_check_ts)",
                (_iso(_now(now)),)).fetchall()
            return [_row_to_goal(r) for r in rows]
    except Exception:
        return []


def update(goal_id: int, *, step_updates: Optional[list] = None,
           next_check_minutes: Optional[float] = None,
           status: Optional[str] = None, result: Optional[str] = None,
           progress_note: Optional[str] = None,
           now: Optional[datetime] = None,
           db_path: Optional[str] = None) -> dict:
    """Advance a goal: mark steps, set the next engagement, log progress, or
    close it out. The headless runs are *required* to call this. Never raises."""
    db_path = db_path or DB_PATH
    t = _now(now)
    g = get(goal_id, db_path=db_path)
    if g is None:
        return {"error": f"no goal #{goal_id}"}
    try:
        steps = g["steps"]
        if step_updates:
            by_n = {s["n"]: s for s in steps}
            for u in step_updates:
                s = by_n.get(int(u.get("n", 0)))
                if not s:
                    continue
                if u.get("status") in ("pending", "done", "failed", "skipped"):
                    s["status"] = u["status"]
                if u.get("note"):
                    s["note"] = str(u["note"])[:300]
        progress = g["progress"]
        if progress_note:
            progress.append({"t": _iso(t), "note": str(progress_note)[:500]})
            progress = progress[-50:]
        sets = ["updated_ts=?", "steps=?", "progress=?"]
        params: list = [_iso(t), json.dumps(steps), json.dumps(progress)]
        if next_check_minutes is not None:
            try:
                mins = max(1.0, min(float(next_check_minutes), 7 * 24 * 60.0))
                sets.append("next_check_ts=?")
                params.append(_iso(t + timedelta(minutes=mins)))
            except (TypeError, ValueError):
                pass
        if status in STATUSES:
            sets.append("status=?")
            params.append(status)
        if result is not None:
            sets.append("last_result=?")
            params.append(str(result)[:1000])
        params.append(int(goal_id))
        with _connect(db_path) as conn:
            conn.execute(f"UPDATE goals SET {', '.join(sets)} WHERE id=?", params)
        return {"ok": True, "id": int(goal_id)}
    except Exception as exc:
        _LOGGER.warning("goals.update failed: %s", exc)
        return {"error": str(exc)}


def cancel(goal_id: int, *, db_path: Optional[str] = None) -> bool:
    db_path = db_path or DB_PATH
    try:
        with _connect(db_path) as conn:
            cur = conn.execute(
                "UPDATE goals SET status='cancelled', updated_ts=? "
                "WHERE id=? AND status='active'",
                (_iso(datetime.now()), int(goal_id)))
            return cur.rowcount > 0
    except Exception:
        return False


def _arm(goal_id: int, when: datetime, runs: int, *,
         db_path: Optional[str] = None) -> None:
    """Pre-arm the next engagement + count the run (crash-safe: set BEFORE the
    run so a mid-run failure can never hot-loop the goal)."""
    db_path = db_path or DB_PATH
    try:
        with _connect(db_path) as conn:
            conn.execute("UPDATE goals SET next_check_ts=?, runs=? WHERE id=?",
                         (_iso(when), runs, int(goal_id)))
    except Exception:
        pass


# ── the goal prompt ──────────────────────────────────────────────────────────

def build_prompt(goal: dict, *, deadline_passed: bool = False) -> str:
    marks = {"pending": " ", "done": "x", "failed": "!", "skipped": "-"}
    lines = [
        f"You are working on YOUR OWN goal #{goal['id']}: {goal['title']}",
        f"OUTCOME to achieve: {goal['outcome']}",
    ]
    if goal.get("deadline_ts"):
        lines.append(f"Deadline: {goal['deadline_ts']}")
    if deadline_passed:
        lines.append("THE DEADLINE HAS PASSED — wrap up now: finish what you "
                      "can immediately, then close the goal (done or failed) "
                      "with an honest result.")
    if goal["steps"]:
        lines.append("Plan:")
        for s in goal["steps"]:
            note = f" — {s['note']}" if s.get("note") else ""
            lines.append(f"  [{marks.get(s['status'], ' ')}] {s['n']}. {s['step']}{note}")
    if goal["progress"]:
        lines.append("Recent progress:")
        for p in goal["progress"][-PROGRESS_TAIL:]:
            lines.append(f"  {p['t']}: {p['note']}")
    lines.append(
        "Advance this goal now: check the relevant states, act where needed, "
        "and verify. Before you finish you MUST call update_goal(goal_id="
        f"{goal['id']}, ...) exactly once to record what happened — mark step "
        "statuses, add a progress_note, and EITHER set next_check_minutes for "
        "when you should re-engage OR set status='done'/'failed' with a result "
        "the user will hear. If nothing needs doing yet, say so in the note and "
        "set a sensible next check.")
    return "\n".join(lines)


# ── execution (async — the core tick calls this) ─────────────────────────────

async def async_process_due(
    hass, config: dict,
    runner: Optional[Callable[[str, str], Awaitable[str]]] = None,
    *, now: Optional[datetime] = None, db_path: Optional[str] = None,
) -> list[dict]:
    """Engage every due goal through the headless agent. Quiet while a goal is
    active; returns announce actions only for goals that FINISH (done/failed).
    Never raises."""
    db_path = db_path or DB_PATH
    actions: list[dict] = []
    t = _now(now)
    try:
        todo = await hass.async_add_executor_job(
            lambda: due(now=t, db_path=db_path))
    except Exception:
        return actions
    if not todo or runner is None:
        return actions

    for g in todo:
        gid = g["id"]
        runs = int(g.get("runs") or 0) + 1
        # Pre-arm the default re-engagement (crash-safe; the model's own
        # update_goal(next_check_minutes) overrides it during the run).
        interval = float(g.get("check_interval_min") or DEFAULT_INTERVAL_MIN)
        await hass.async_add_executor_job(
            lambda: _arm(gid, t + timedelta(minutes=interval), runs,
                         db_path=db_path))

        if runs > MAX_RUNS:
            await hass.async_add_executor_job(
                lambda: update(gid, status="failed",
                               result=f"I've engaged this goal {MAX_RUNS} times "
                                      f"without completing it, so I'm setting it "
                                      f"aside: {g['title']}",
                               progress_note="run budget exhausted",
                               now=t, db_path=db_path))
            g2 = await hass.async_add_executor_job(
                lambda: get(gid, db_path=db_path))
            actions.append(_finish_action(g2))
            continue

        deadline_passed = bool(g.get("deadline_ts")) and \
            _iso(t) > str(g["deadline_ts"])
        prompt = build_prompt(g, deadline_passed=deadline_passed)
        try:
            result = (await runner(prompt, "") or "").strip()
        except Exception as exc:
            _LOGGER.warning("goal #%s run failed: %s", gid, exc)
            await hass.async_add_executor_job(
                lambda e=str(exc): update(gid, progress_note=f"run error: {e}",
                                          now=t, db_path=db_path))
            continue      # stays active; pre-armed next check stands

        g2 = await hass.async_add_executor_job(lambda: get(gid, db_path=db_path))
        if g2 is None:
            continue
        if deadline_passed and g2["status"] == "active":
            # The model was told to close it and didn't — close it honestly.
            await hass.async_add_executor_job(
                lambda: update(gid, status="failed",
                               result=result or f"The deadline for "
                                                f"'{g['title']}' passed.",
                               progress_note="deadline passed — force-closed",
                               now=t, db_path=db_path))
            g2 = await hass.async_add_executor_job(
                lambda: get(gid, db_path=db_path))

        try:
            from . import database
            database.save_activity(
                category="goal", urgency="low",
                message=f"Goal #{gid} '{g['title']}' engaged (run {runs}): "
                        f"{(result or 'no report')[:160]}",
                source="agent")
        except Exception:
            pass

        if g2["status"] in ("done", "failed"):
            actions.append(_finish_action(g2))
        # still active → quiet by design

    return actions


def _finish_action(goal: Optional[dict]) -> dict:
    goal = goal or {}
    verdict = goal.get("last_result") or (
        f"Goal '{goal.get('title', '')}' is "
        f"{'complete' if goal.get('status') == 'done' else 'closed'}.")
    return {"type": "goal", "urgency": "medium", "message": verdict,
            "auto_act": True}
