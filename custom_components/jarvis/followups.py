"""
JARVIS follow-ups — the agent scheduling its *own* future actions.

This is the substrate that makes JARVIS agentic across time rather than only
within a single turn. The model can decide "I'll check the garage actually
closed in five minutes", "re-check the temperature after the heat has had time
to work", or "remind about the oven in an hour" — and the cognitive core's loop
executes the instruction later by running it back through the same agent brain
(run_agent), headlessly. Results are announced through the normal proactive
channel, so quiet hours and urgency routing all apply.

Storage is a small table in patterns.db (same one-DB story as the state logger
and cognition model). SYNC sqlite — call via executor. Never raises.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

_LOGGER = logging.getLogger(__name__)

DB_PATH = "/config/jarvis/patterns.db"
MAX_OPEN = 25                 # safety valve: the agent can't queue unbounded work
MAX_DELAY_MINUTES = 7 * 24 * 60   # a week out, at most
STATUSES = ("pending", "done", "cancelled", "failed")


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_ts TEXT NOT NULL,
            due_ts TEXT NOT NULL,
            instruction TEXT NOT NULL,
            context TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            result TEXT DEFAULT ''
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fu_due ON followups(status, due_ts)")
    return conn


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now()


# ── CRUD (sync — executor) ───────────────────────────────────────────────────

def schedule(instruction: str, delay_minutes: float, *, context: str = "",
             now: Optional[datetime] = None, db_path: Optional[str] = None) -> dict:
    """Queue a self-directed follow-up. Returns the row, or an 'error' dict
    (queue full / bad input) — never raises."""
    instruction = (instruction or "").strip()
    if not instruction:
        return {"error": "empty instruction"}
    try:
        delay = max(0.1, min(float(delay_minutes), MAX_DELAY_MINUTES))
    except (TypeError, ValueError):
        return {"error": f"bad delay: {delay_minutes!r}"}
    db_path = db_path or DB_PATH
    t = _now(now)
    due = t + timedelta(minutes=delay)
    try:
        with _connect(db_path) as conn:
            open_n = conn.execute(
                "SELECT COUNT(*) FROM followups WHERE status='pending'"
            ).fetchone()[0]
            if open_n >= MAX_OPEN:
                return {"error": f"follow-up queue is full ({MAX_OPEN} pending)"}
            cur = conn.execute(
                "INSERT INTO followups (created_ts, due_ts, instruction, context) "
                "VALUES (?,?,?,?)",
                (t.isoformat(sep=" "), due.isoformat(sep=" "), instruction, context))
            return {"id": cur.lastrowid, "due_ts": due.isoformat(sep=" "),
                    "instruction": instruction}
    except Exception as exc:
        _LOGGER.warning("followups.schedule failed: %s", exc)
        return {"error": str(exc)}


def pending(*, now: Optional[datetime] = None, due_only: bool = False,
            db_path: Optional[str] = None) -> list[dict]:
    """Open follow-ups; with due_only, just those whose time has arrived."""
    db_path = db_path or DB_PATH
    try:
        with _connect(db_path) as conn:
            if due_only:
                rows = conn.execute(
                    "SELECT * FROM followups WHERE status='pending' AND "
                    "datetime(due_ts) <= datetime(?) ORDER BY datetime(due_ts)",
                    (_now(now).isoformat(sep=" "),)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM followups WHERE status='pending' "
                    "ORDER BY datetime(due_ts)").fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def cancel(followup_id: int, *, db_path: Optional[str] = None) -> bool:
    db_path = db_path or DB_PATH
    try:
        with _connect(db_path) as conn:
            cur = conn.execute(
                "UPDATE followups SET status='cancelled' "
                "WHERE id=? AND status='pending'", (int(followup_id),))
            return cur.rowcount > 0
    except Exception:
        return False


def mark(followup_id: int, status: str, result: str = "",
         *, db_path: Optional[str] = None) -> None:
    if status not in STATUSES:
        return
    db_path = db_path or DB_PATH
    try:
        with _connect(db_path) as conn:
            conn.execute("UPDATE followups SET status=?, result=? WHERE id=?",
                         (status, result[:2000], int(followup_id)))
    except Exception as exc:
        _LOGGER.debug("followups.mark failed: %s", exc)


# ── execution (async — the core tick calls this) ─────────────────────────────

async def async_process_due(
    hass, config: dict,
    runner: Optional[Callable[[str, str], Awaitable[str]]] = None,
    *, now: Optional[datetime] = None, db_path: Optional[str] = None,
) -> list[dict]:
    """Execute every due follow-up through `runner(instruction, context)` (the
    headless agent). Returns proactive action dicts for the normal emission
    path — quiet hours and urgency routing apply there. Never raises."""
    actions: list[dict] = []
    db_path = db_path or DB_PATH
    try:
        due = await hass.async_add_executor_job(
            lambda: pending(now=now, due_only=True, db_path=db_path))
    except Exception:
        return actions
    if not due:
        return actions
    if runner is None:
        for row in due:
            await hass.async_add_executor_job(
                lambda r=row: mark(r["id"], "failed", "no runner available",
                                   db_path=db_path))
        return actions

    for row in due:
        try:
            result = await runner(row["instruction"], row.get("context") or "")
            result = (result or "").strip() or "Done."
            await hass.async_add_executor_job(
                lambda r=row, res=result: mark(r["id"], "done", res,
                                               db_path=db_path))
            actions.append({
                "type": "followup",
                "urgency": "medium",
                "message": result,
                "auto_act": True,
            })
            try:
                from . import database
                database.save_activity(
                    category="followup", urgency="low",
                    message=f"Follow-up #{row['id']} ran: "
                            f"{row['instruction'][:120]} → {result[:160]}",
                    source="agent")
            except Exception:
                pass
        except Exception as exc:
            _LOGGER.warning("followup #%s failed: %s", row.get("id"), exc)
            await hass.async_add_executor_job(
                lambda r=row, e=str(exc): mark(r["id"], "failed", e,
                                               db_path=db_path))
    return actions
