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

from .paths import config_path_str
from .sqlite_utils import ClosingConnection

_LOGGER = logging.getLogger(__name__)

DB_PATH = config_path_str("jarvis", "patterns.db")
MAX_ACTIVE = 10            # concurrent outcomes JARVIS may pursue
MAX_RUNS = 120             # per-goal engagement budget (runaway guard)
DEFAULT_INTERVAL_MIN = 30  # re-engage cadence when the model doesn't set one
MAX_DEADLINE_MIN = 30 * 24 * 60   # a month out, at most
PROGRESS_TAIL = 5          # progress entries shown in the goal prompt
STATUSES = ("active", "done", "failed", "cancelled")


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, factory=ClosingConnection)
    try:
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
    except Exception:
        conn.close()
        raise


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
            _shadow_plan(cur.lastrowid, title, outcome, norm_steps)   # F1: mirror into kernel.plan
            return {"id": cur.lastrowid, "title": title, "outcome": outcome,
                    "steps": norm_steps, "next_check_ts": _iso(t),
                    "deadline_ts": deadline}
    except Exception as exc:
        _LOGGER.warning("goals.create failed: %s", exc)
        return {"error": str(exc)}


_journal = None   # lazily-built kernel.journal.ExecutionJournal (F2)


def _get_journal():
    global _journal
    if _journal is None:
        from .kernel.journal import ExecutionJournal
        _journal = ExecutionJournal(config_path_str("jarvis", "journal.db"))
    return _journal


def _goal_plan_id(goal_id: int) -> str:
    """Deterministic kernel plan_id for a goal, so the goal's lifecycle (create →
    step advances → completion) addresses the SAME journal rows across calls."""
    return f"goalplan_{int(goal_id)}"


def _goal_step_id(goal_id: int, n) -> str:
    return f"goalstep_{int(goal_id)}_{n}"


def _shadow_plan(goal_id: int, title: str, outcome: str, norm_steps: list) -> None:
    """MCU Phase F (F1 + F2) / Phase H (H4a): express a goal's ordered steps as a
    kernel ``Plan`` in SHADOW and durably journal it, keyed by DETERMINISTIC ids
    derived from the goal id so the whole goal lifecycle lands on the same rows.

    F1 builds the Plan alongside the goal (a goal is a plan pursued across time).
    F2 records that Plan into the kernel execution journal (``record_plan``) so
    the goal → plan → step chain is durably reconstructable — the foundation for
    agency recovery after a restart. Under H4b this recorded Plan is the
    AUTHORITATIVE seed for the goal's step state (see ``_apply_journal_authority``);
    the row is cleared first (``forget_plan``) so a reused goal id starts clean.
    Best-effort — never affects goal creation."""
    try:
        from .kernel import plan as P
        steps = tuple(
            P.Step(action=s.get("step", ""),
                   params={"n": s.get("n"), "status": s.get("status", "pending")},
                   idempotency_key=_goal_step_id(goal_id, s.get("n")),
                   id=_goal_step_id(goal_id, s.get("n")))
            for s in (norm_steps or [])
        )
        plan = P.Plan(goal=(title or outcome), steps=steps,
                      id=_goal_plan_id(goal_id))
        _LOGGER.debug("goal shadow plan: %s with %d step(s) [%s]",
                      plan.goal, len(plan.steps), plan.id)
        if plan.steps:
            jr = None
            try:
                jr = _get_journal()
            except Exception:   # pragma: no cover - defensive
                jr = None
            if jr is not None:
                # H4b: a goal id can be reused after the goal store is reset, and
                # record_plan is INSERT-OR-IGNORE — so clear any prior rows for
                # this deterministic plan_id first. The journal is now
                # authoritative for goal step state; a (re)seeded goal must start
                # from its own clean plan, never inherit a stale run's statuses.
                # Isolated so a journal without forget_plan still records.
                try:
                    jr.forget_plan(plan.id)
                except Exception:   # pragma: no cover - defensive
                    pass
                try:
                    jr.record_plan(plan)   # F2: durable authoritative seed
                except Exception:   # pragma: no cover - defensive
                    pass
    except Exception:   # pragma: no cover - defensive
        pass


def _journal_goal_progress(goal_id: int, steps: list,
                           status: Optional[str] = None) -> None:
    """MCU Phase H (H4a): record a goal's step transitions into the kernel
    execution journal as they happen — so the journal reconstructs not just that a
    goal was *planned* (F2) but what actually *happened* to it (Phase H exit
    criterion: every consequential agency operation is reconstructable from the
    journal). For each step in a terminal state (done/failed/skipped) the matching
    journal row is finished with that status; when the goal itself closes
    (done/failed/cancelled) any still-pending steps are marked skipped. SHADOW /
    best-effort — the goal store stays authoritative and this never affects the
    update it records."""
    try:
        jr = _get_journal()
        pid = _goal_plan_id(goal_id)
        goal_closed = status in ("done", "failed", "cancelled")
        for s in (steps or []):
            st = str(s.get("status", "pending"))
            n = s.get("n")
            if st in ("done", "failed", "skipped"):
                jr.finish_step(pid, _goal_step_id(goal_id, n), st,
                               str(s.get("note", ""))[:300])
            elif goal_closed:
                # goal is closing with this step still open → it won't run
                jr.finish_step(pid, _goal_step_id(goal_id, n), "skipped",
                               "goal closed before step completed")
    except Exception:   # pragma: no cover - defensive
        pass


# MCU Phase F/H — plan PARITY. The goal's kernel Plan is built + journaled
# (F1/F2) and kept in step-by-step sync with the goal store (H4a). This consults
# that durable Plan on every engagement and logs whether it AGREES with the goal
# store. Under H4b (below) the journal is authoritative and the store is its
# mirror, so this now reads as a continuous invariant check — they should always
# agree; a logged divergence means a write landed in one store but not the other.
_PLAN_PARITY = {"agree": 0, "disagree": 0}


def _plan_parity(goal: dict) -> Optional[bool]:
    """Compare the kernel journal's view of a goal's steps against the live goal
    store. Returns True (agree), False (diverged), or None (couldn't compare).
    Log-only; keeps a running tally for observability. Never raises."""
    try:
        gid = int(goal.get("id"))
        store_steps = goal.get("steps") or []
        if not store_steps:
            return None
        journaled = {js.step_id: str(js.status).lower()
                     for js in _get_journal().steps(_goal_plan_id(gid))}
        if not journaled:
            return None                      # nothing journaled yet to compare
        diverged = []
        for s in store_steps:
            sid = _goal_step_id(gid, s.get("n"))
            want = str(s.get("status", "pending")).lower()
            if journaled.get(sid) != want:
                diverged.append((sid, want, journaled.get(sid)))
        if diverged:
            _PLAN_PARITY["disagree"] += 1
            _LOGGER.info("goal plan parity: #%s kernel Plan diverges from the "
                         "goal store on %d step(s): %s", gid, len(diverged),
                         diverged[:5])
            return False
        _PLAN_PARITY["agree"] += 1
        _LOGGER.debug("goal plan parity: #%s kernel Plan agrees with the goal "
                      "store (%d step(s))", gid, len(store_steps))
        return True
    except Exception:   # pragma: no cover - defensive, log-only
        return None


# ── MCU Phase H (H4b): the kernel Plan is AUTHORITATIVE over goal step state ──
# Parity (above) proved the durable kernel Plan faithfully tracks a goal. H4b
# promotes it: the kernel journal is now the SOURCE OF TRUTH for a goal's
# per-step status, and the goal store is kept as its current MIRROR.
#   * create → the journal plan is (re)seeded cleanly (forget + record).
#   * update → the step state is committed to the JOURNAL first (authoritative),
#     then projected into the store.
#   * read   → the journal's statuses overlay the store's when the journal has
#     the goal's rows; when it doesn't (a pre-H4b goal, or journal.db lost/reset)
#     the store stands as the fallback, so goal state is NEVER lost.
# In the normal flow store and journal are written together and agree, so the
# overlay is a no-op; it only bites when the two genuinely diverge, and then the
# journal — the authority — wins. Reversible: `goals_kernel_authoritative`
# (config, default on) falls the whole path back to store-authoritative parity.


def _kernel_authoritative() -> bool:
    """Whether the kernel journal is authoritative for goal step state (H4b).
    Default on; set ``goals_kernel_authoritative=false`` to revert to the
    store-authoritative parity behaviour. Cheap (in-memory config); never raises."""
    try:
        from . import jarvis_config
        return bool(jarvis_config.get("goals_kernel_authoritative", True))
    except Exception:   # pragma: no cover - config unavailable → keep default on
        return True


def _journal_sync_steps(goal_id: int, steps: list) -> None:
    """H4b authoritative write: mirror each step's status into the journal
    verbatim, so an authoritative read agrees with what the store is about to be
    set to. Committed BEFORE the store write (the journal is the authority).
    Best-effort; never raises."""
    try:
        jr = _get_journal()
        pid = _goal_plan_id(goal_id)
        for s in (steps or []):
            jr.finish_step(pid, _goal_step_id(goal_id, s.get("n")),
                           str(s.get("status", "pending")),
                           str(s.get("note", ""))[:300])
    except Exception:   # pragma: no cover - defensive
        pass


def _apply_journal_authority(goal: Optional[dict]) -> Optional[dict]:
    """H4b read: overlay the journal's authoritative per-step status onto a
    goal's store steps (in place) when the kernel is authoritative AND the
    journal holds this goal's rows. When the journal has no rows for the goal
    (pre-H4b, or journal.db lost/reset) the store steps are left untouched — the
    mirror is the fallback, so goal state is never lost. Returns ``goal``.
    Never raises."""
    try:
        if goal is None or not _kernel_authoritative():
            return goal
        gid = int(goal.get("id"))
        steps = goal.get("steps") or []
        if not steps:
            return goal
        journaled = {js.step_id: str(js.status).lower()
                     for js in _get_journal().steps(_goal_plan_id(gid))}
        if not journaled:
            return goal                      # journal absent → store authoritative
        for s in steps:
            jst = journaled.get(_goal_step_id(gid, s.get("n")))
            # Only the goal's own step vocabulary; the journal's internal
            # lifecycle states (running/needs_replay/…) never overwrite a step.
            if jst in ("pending", "done", "failed", "skipped"):
                s["status"] = jst
    except Exception:   # pragma: no cover - defensive, best-effort
        pass
    return goal


def get(goal_id: int, *, db_path: Optional[str] = None) -> Optional[dict]:
    db_path = db_path or DB_PATH
    try:
        with _connect(db_path) as conn:
            r = conn.execute("SELECT * FROM goals WHERE id=?",
                             (int(goal_id),)).fetchone()
            g = _row_to_goal(r) if r else None
        # H4b: the kernel journal is authoritative for step state when present.
        return _apply_journal_authority(g)
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


def recent(*, limit: int = 20, db_path: Optional[str] = None) -> list[dict]:
    """All goals regardless of status (active + done/failed/cancelled),
    most recently updated first — the panel's history view. active() only
    ever returns active ones, which can't show what JARVIS just finished."""
    db_path = db_path or DB_PATH
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM goals ORDER BY datetime(updated_ts) DESC LIMIT ?",
                (max(1, min(int(limit), 100)),)).fetchall()
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
        # H4b: on close, abandoned (still-pending) steps become skipped — the
        # goal's terminal state — written to BOTH the authoritative journal and
        # its store mirror (below) so they agree and the plan reads as complete.
        # Parity (flag off) leaves the store steps as-is and marks skips in the
        # journal only, via _journal_goal_progress (the pre-H4b behaviour).
        if _kernel_authoritative() and status in ("done", "failed", "cancelled"):
            for s in steps:
                if str(s.get("status", "pending")) == "pending":
                    s["status"] = "skipped"
                    if not s.get("note"):
                        s["note"] = "goal closed before step completed"
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
        _st = status if status in STATUSES else None

        def _commit_store():
            with _connect(db_path) as conn:
                conn.execute(f"UPDATE goals SET {', '.join(sets)} WHERE id=?",
                             params)

        if _kernel_authoritative():
            # H4b: the kernel journal is the authority — commit the step state to
            # it FIRST, then mirror the goal store. If journal.db were lost the
            # store write still lands (the mirror is the durable fallback).
            _journal_sync_steps(int(goal_id), steps)
            _commit_store()
        else:
            # Parity (flag off): the store is authoritative; the journal mirrors
            # the transitions afterward (H4a behaviour).
            _commit_store()
            _journal_goal_progress(int(goal_id), steps, _st)
        return {"ok": True, "id": int(goal_id)}
    except Exception as exc:
        _LOGGER.warning("goals.update failed: %s", exc)
        return {"error": str(exc)}


def cancel(goal_id: int, *, db_path: Optional[str] = None) -> bool:
    db_path = db_path or DB_PATH
    try:
        g = get(goal_id, db_path=db_path)
        if g is None or g.get("status") != "active":
            return False
        steps = g.get("steps") or []
        authoritative = _kernel_authoritative()
        if authoritative:
            # H4b: abandoned steps become skipped in BOTH the store mirror and
            # the authoritative journal, so a cancelled goal reads as complete.
            for s in steps:
                if str(s.get("status", "pending")) == "pending":
                    s["status"] = "skipped"
                    if not s.get("note"):
                        s["note"] = "goal cancelled before step completed"
            sql = ("UPDATE goals SET status='cancelled', updated_ts=?, steps=? "
                   "WHERE id=? AND status='active'")
            sql_params = (_iso(datetime.now()), json.dumps(steps), int(goal_id))
        else:
            # Parity (flag off): store keeps the steps as-is (pre-H4b behaviour).
            sql = ("UPDATE goals SET status='cancelled', updated_ts=? "
                   "WHERE id=? AND status='active'")
            sql_params = (_iso(datetime.now()), int(goal_id))
        with _connect(db_path) as conn:
            cur = conn.execute(sql, sql_params)
            cancelled = cur.rowcount > 0
        if cancelled:
            # Record the cancellation in the kernel journal so the goal's
            # terminal state is reconstructable (H4b mirrors the skipped steps;
            # parity marks open steps skipped in the journal only, via H4a).
            if authoritative:
                _journal_sync_steps(int(goal_id), steps)
            else:
                _journal_goal_progress(int(goal_id), steps, "cancelled")
        return cancelled
    except Exception:
        return False


def delete(goal_id: int, *, db_path: Optional[str] = None) -> bool:
    """Permanently remove a goal row (any status). Unlike cancel(), which keeps
    the row for history, this hard-deletes it — for tidying the goals list.
    Returns True if a row was removed. Never raises."""
    db_path = db_path or DB_PATH
    try:
        with _connect(db_path) as conn:
            cur = conn.execute("DELETE FROM goals WHERE id=?", (int(goal_id),))
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

def _emit_goal_event(hass, action: str, goal: Optional[dict],
                     *, status: Optional[str] = None) -> None:
    """Publish a goal lifecycle transition on the kernel event bus (parity,
    best-effort). The goal store stays authoritative; this only mirrors the
    engage/close onto the bus so goals enter the event stream the same way
    situations and actuations do — no consumer reacts to it yet. Never raises."""
    try:
        from . import events
        g = goal or {}
        events.publish_goal(
            hass, action,
            goal_id=g.get("id"), title=g.get("title"),
            status=status if status is not None else g.get("status"))
    except Exception:   # pragma: no cover - defensive
        pass


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
            _emit_goal_event(hass, "closed", g2, status="failed")
            continue

        deadline_passed = bool(g.get("deadline_ts")) and \
            _iso(t) > str(g["deadline_ts"])
        _emit_goal_event(hass, "engaged", g, status="active")
        # H4b: the kernel journal is authoritative for step state — overlay it
        # onto this goal before the agent (and parity) see it, so the prompt
        # reflects the source of truth. Best-effort; a no-op when store and
        # journal already agree (the normal case) or the flag is off.
        try:
            await hass.async_add_executor_job(
                lambda gg=g: _apply_journal_authority(gg))
        except Exception:   # pragma: no cover - defensive
            pass
        # F/H plan parity (log-only): confirm the durable kernel Plan and the
        # goal-store mirror agree on this engagement.
        try:
            await hass.async_add_executor_job(lambda gg=g: _plan_parity(gg))
        except Exception:   # pragma: no cover - defensive, log-only
            pass
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
            await hass.async_add_executor_job(
                lambda: database.save_activity(
                    category="goal", urgency="low",
                    message=f"Goal #{gid} '{g['title']}' engaged (run {runs}): "
                            f"{(result or 'no report')[:160]}",
                    source="agent")
            )
        except Exception:
            pass

        if g2["status"] in ("done", "failed"):
            actions.append(_finish_action(g2))
            _emit_goal_event(hass, "closed", g2)
        # still active → quiet by design

    return actions


def _finish_action(goal: Optional[dict]) -> dict:
    goal = goal or {}
    verdict = goal.get("last_result") or (
        f"Goal '{goal.get('title', '')}' is "
        f"{'complete' if goal.get('status') == 'done' else 'closed'}.")
    return {"type": "goal", "urgency": "medium", "message": verdict,
            "auto_act": True}
