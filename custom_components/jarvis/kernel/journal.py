"""Execution journal + crash recovery (kernel hardening H4, docs/KERNEL_PLAN.md).

The planner (`kernel.plan`) runs a plan step-by-step in memory. If the process
dies mid-plan — a restart, a crash, a power blip — that in-memory progress is
lost, and on the next boot nobody knows whether the step that was *running* at the
time actually took effect. The audit flagged this: JARVIS can act, but it cannot
pick up where it left off.

This module is the durable record. Each step's lifecycle is written through
``kernel.persistence`` as it happens:

    record_plan → start_step(RUNNING) → finish_step(DONE / FAILED / …)

so after a restart ``in_flight()`` returns exactly the steps that were started but
never resolved. :func:`recover` then re-checks each against live state (via an
injected ``verify`` callable — "did this actually happen?") and settles it:
verified → DONE, otherwise flagged ``NEEDS_REPLAY`` for the caller to re-run,
honouring the plan's idempotency so recovery never double-acts.

Storage goes through the one persistence seam (its own DB file, its own tables —
no schema merge). No Home Assistant import; the live checks are injected, so the
journal and recovery are deterministic and unit-testable on a real temp DB.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from . import persistence

_LOGGER = logging.getLogger(__name__)

# ── step lifecycle states (superset of plan.py's outcomes) ───────────────────────
PENDING = "pending"
RUNNING = "running"          # started, not yet resolved — the crash-recovery set
DONE = "done"
FAILED = "failed"
VERIFY_FAILED = "verify_failed"
SKIPPED = "skipped"
BLOCKED = "blocked"
NEEDS_REPLAY = "needs_replay"  # was RUNNING at crash and could not be verified

# A step is "settled" once it reaches one of these — recovery leaves it alone.
_TERMINAL = frozenset({DONE, FAILED, VERIFY_FAILED, SKIPPED, BLOCKED})

_TABLE = "journal_steps"

_MIGRATIONS = [
    lambda conn: conn.execute(
        f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
        " plan_id TEXT NOT NULL,"
        " step_id TEXT NOT NULL,"
        " seq INTEGER NOT NULL,"
        " action TEXT NOT NULL,"
        " idempotency_key TEXT,"
        " status TEXT NOT NULL,"
        " detail TEXT NOT NULL DEFAULT '',"
        " correlation_id TEXT,"
        " created_ts REAL NOT NULL,"
        " updated_ts REAL NOT NULL,"
        " PRIMARY KEY (plan_id, step_id))"
    ),
    lambda conn: conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_status ON {_TABLE} (status)"
    ),
]


@dataclass(frozen=True)
class JournaledStep:
    """One row of the journal, as read back."""

    plan_id: str
    step_id: str
    seq: int
    action: str
    idempotency_key: Optional[str]
    status: str
    detail: str
    correlation_id: Optional[str]
    created_ts: float
    updated_ts: float


@dataclass(frozen=True)
class RecoveryReport:
    """What :func:`recover` did on one pass."""

    verified: int = 0       # RUNNING steps confirmed complete → DONE
    needs_replay: int = 0   # RUNNING steps not confirmed → NEEDS_REPLAY
    replayed: int = 0       # of those, re-run via the injected ``act``
    checked: int = 0        # total in-flight steps examined

    def to_dict(self) -> dict:
        return {
            "checked": self.checked,
            "verified": self.verified,
            "needs_replay": self.needs_replay,
            "replayed": self.replayed,
        }


class ExecutionJournal:
    """Durable plan/step lifecycle journal backed by ``kernel.persistence``."""

    def __init__(self, db_path: str, *, now: Optional[Callable[[], float]] = None) -> None:
        self._db_path = str(db_path)
        self._now = now or time.time
        persistence.run_migrations(self._db_path, _MIGRATIONS)

    # ── writing the lifecycle ────────────────────────────────────────────────
    def record_plan(self, plan) -> None:
        """Insert every step of ``plan`` as PENDING (idempotent per step).

        ``plan`` is a ``kernel.plan.Plan`` (duck-typed: ``id``, ``steps``,
        optional ``correlation_id``); each step needs ``id``, ``action`` and
        optional ``idempotency_key``.
        """
        ts = self._now()
        corr = getattr(plan, "correlation_id", None)
        with persistence.transaction(self._db_path) as conn:
            for seq, step in enumerate(plan.steps):
                conn.execute(
                    f"INSERT OR IGNORE INTO {_TABLE} "
                    "(plan_id, step_id, seq, action, idempotency_key, status, "
                    " detail, correlation_id, created_ts, updated_ts) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (plan.id, step.id, seq, step.action,
                     getattr(step, "idempotency_key", None), PENDING, "",
                     corr, ts, ts),
                )

    def start_step(self, plan_id: str, step_id: str) -> None:
        """Mark a step RUNNING just before its action is attempted."""
        self._set_status(plan_id, step_id, RUNNING, "")

    def finish_step(self, plan_id: str, step_id: str, status: str,
                    detail: str = "") -> None:
        """Record a step's terminal (or any) outcome after it runs."""
        self._set_status(plan_id, step_id, status, detail)

    def _set_status(self, plan_id: str, step_id: str, status: str,
                    detail: str) -> None:
        ts = self._now()
        with persistence.transaction(self._db_path) as conn:
            cur = conn.execute(
                f"UPDATE {_TABLE} SET status = ?, detail = ?, updated_ts = ? "
                "WHERE plan_id = ? AND step_id = ?",
                (status, detail, ts, plan_id, step_id),
            )
            if cur.rowcount == 0:
                _LOGGER.debug("journal: no row for %s/%s to set %s",
                              plan_id, step_id, status)

    # ── reading state ────────────────────────────────────────────────────────
    def _rows(self, where: str = "", params: Sequence = ()) -> List[JournaledStep]:
        sql = (
            f"SELECT plan_id, step_id, seq, action, idempotency_key, status, "
            f"detail, correlation_id, created_ts, updated_ts FROM {_TABLE} "
            f"{where} ORDER BY plan_id, seq"
        )
        conn = persistence.connect(self._db_path)
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()
        return [JournaledStep(*r) for r in rows]

    def in_flight(self) -> List[JournaledStep]:
        """Steps left RUNNING — started but never resolved (the recovery set)."""
        return self._rows("WHERE status = ?", (RUNNING,))

    def pending(self, plan_id: str) -> List[JournaledStep]:
        """Steps of a plan not yet in a terminal state."""
        marks = ",".join("?" * len(_TERMINAL))
        return self._rows(
            f"WHERE plan_id = ? AND status NOT IN ({marks})",
            (plan_id, *sorted(_TERMINAL)),
        )

    def steps(self, plan_id: str) -> List[JournaledStep]:
        return self._rows("WHERE plan_id = ?", (plan_id,))

    def plan_complete(self, plan_id: str) -> bool:
        """True if every recorded step of the plan is terminal (and there is ≥1)."""
        rows = self.steps(plan_id)
        return bool(rows) and all(r.status in _TERMINAL for r in rows)

    def forget_plan(self, plan_id: str) -> int:
        """Delete every recorded step of ``plan_id``. Returns rows removed.

        For a DETERMINISTIC plan_id that is (re)seeded — e.g. a goal id reused
        after its store was reset — this clears any prior run's rows first, so
        ``record_plan`` cannot leave a fresh plan inheriting stale step statuses
        (``record_plan`` is INSERT-OR-IGNORE and would otherwise keep them). A
        genuinely append-only caller never calls this."""
        with persistence.transaction(self._db_path) as conn:
            cur = conn.execute(
                f"DELETE FROM {_TABLE} WHERE plan_id = ?", (plan_id,))
            return cur.rowcount if cur.rowcount is not None else 0


# Injected callables for recovery:
#   verify(step) -> bool   "has this step's effect actually taken hold?"
#   act(step)    -> truthy  optionally re-run a step whose effect is missing
Verify = Callable[[JournaledStep], bool]
Act = Callable[[JournaledStep], object]


def recover(
    journal: ExecutionJournal,
    *,
    verify: Verify,
    act: Optional[Act] = None,
) -> RecoveryReport:
    """Settle every in-flight step after a restart.

    For each step left RUNNING: ``verify(step)`` asks live state whether it
    actually completed. If so it is marked DONE. Otherwise, when ``act`` is
    given, the step is re-run (idempotency in the real actuator makes this safe)
    and marked DONE/NEEDS_REPLAY by the result; without ``act`` it is left
    NEEDS_REPLAY for the caller to handle. A ``verify``/``act`` that raises is
    treated as "not recovered" (NEEDS_REPLAY), never fatal.
    """
    verified = replayed = needs_replay = 0
    stuck = journal.in_flight()
    for step in stuck:
        try:
            done = bool(verify(step))
        except Exception as exc:
            _LOGGER.debug("journal.recover: verify raised for %s/%s: %s",
                          step.plan_id, step.step_id, exc)
            done = False

        if done:
            journal.finish_step(step.plan_id, step.step_id, DONE,
                                "verified on recovery")
            verified += 1
            continue

        if act is not None:
            try:
                if act(step):
                    journal.finish_step(step.plan_id, step.step_id, DONE,
                                        "replayed on recovery")
                    replayed += 1
                    verified += 1
                    continue
            except Exception as exc:
                _LOGGER.debug("journal.recover: act raised for %s/%s: %s",
                              step.plan_id, step.step_id, exc)

        journal.finish_step(step.plan_id, step.step_id, NEEDS_REPLAY,
                            "unverified after restart")
        needs_replay += 1

    return RecoveryReport(
        verified=verified, needs_replay=needs_replay,
        replayed=replayed, checked=len(stuck),
    )
