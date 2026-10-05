"""Continuity of self — durable agency state + restart recovery (roadmap Phase I).

The execution journal (``kernel.journal``, H4) already recovers in-flight *plan
steps* after a restart. But JARVIS's sense of what it is *in the middle of* — the
goals it is pursuing, the situations it has open, the operating mode it is in —
lives only in memory and is lost on every reboot. This module is the durable
record of that **agency state**: a small, versioned snapshot of JARVIS's ongoing
commitments, written through the one persistence seam, so that after a restart
JARVIS can know (and later resume) what it was doing rather than waking up blank.

Phase I lands this primitive **pure**: it captures, stores and reloads the
snapshot, renders a human/log *continuity summary*, and reconciles a reloaded
snapshot against what is still live — but nothing live is wired to it yet
(bootstrap capture is I2/shadow; boot-time reconcile is I3/parity; resuming or
announcing continuity is I4/enforce). No Home Assistant import — callers pass
plain extracted data — so it is deterministic and unit-testable on a real temp DB.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Mapping, Optional, Tuple

from . import persistence

_LOGGER = logging.getLogger(__name__)

# Bump when the persisted payload shape changes incompatibly. A snapshot written
# by a newer schema is ignored on load (fail-safe to "no prior state") rather than
# mis-parsed — continuity is best-effort and must never raise into the boot path.
SCHEMA_VERSION = 1

# Commitment kinds JARVIS currently tracks. Open-ended on purpose: unknown kinds
# round-trip untouched so a newer capture site can add one without a migration.
GOAL = "goal"
SITUATION = "situation"

_TABLE = "agency_snapshots"

_MIGRATIONS = [
    lambda conn: conn.execute(
        f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " captured_ts REAL NOT NULL,"
        " schema_version INTEGER NOT NULL,"
        " payload TEXT NOT NULL)"
    ),
    lambda conn: conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_captured ON {_TABLE} (captured_ts)"
    ),
]


@dataclass(frozen=True)
class Commitment:
    """One thing JARVIS is in the middle of — a pursued goal, an open situation."""

    kind: str
    id: str
    label: str = ""
    status: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, d: Mapping) -> "Commitment":
        return cls(
            kind=str(d.get("kind", "")),
            id=str(d.get("id", "")),
            label=str(d.get("label", "")),
            status=str(d.get("status", "")),
            detail=str(d.get("detail", "")),
        )


@dataclass(frozen=True)
class AgencyState:
    """A point-in-time snapshot of JARVIS's ongoing agency."""

    captured_ts: float
    mode: Optional[str] = None
    commitments: Tuple[Commitment, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def of_kind(self, kind: str) -> List[Commitment]:
        return [c for c in self.commitments if c.kind == kind]

    @property
    def goals(self) -> List[Commitment]:
        return self.of_kind(GOAL)

    @property
    def situations(self) -> List[Commitment]:
        return self.of_kind(SITUATION)

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "captured_ts": self.captured_ts,
                "mode": self.mode,
                "commitments": [c.to_dict() for c in self.commitments],
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> "AgencyState":
        d = json.loads(raw)
        comms = tuple(
            Commitment.from_dict(c) for c in (d.get("commitments") or [])
            if isinstance(c, Mapping)
        )
        return cls(
            captured_ts=float(d.get("captured_ts", 0.0)),
            mode=d.get("mode"),
            commitments=comms,
            schema_version=int(d.get("schema_version", SCHEMA_VERSION)),
        )


def _commitments_from(kind: str, rows: Iterable[Mapping]) -> List[Commitment]:
    out: List[Commitment] = []
    for r in rows or ():
        if not isinstance(r, Mapping):
            continue
        cid = r.get("id")
        if cid is None or str(cid) == "":
            continue  # a commitment with no stable id can't be reconciled; skip
        out.append(
            Commitment(
                kind=kind,
                id=str(cid),
                label=str(r.get("label", "") or ""),
                status=str(r.get("status", "") or ""),
                detail=str(r.get("detail", "") or ""),
            )
        )
    return out


def capture(
    *,
    mode: Optional[str] = None,
    goals: Iterable[Mapping] = (),
    situations: Iterable[Mapping] = (),
    extra: Iterable[Commitment] = (),
    now: Optional[Callable[[], float]] = None,
) -> AgencyState:
    """Build an :class:`AgencyState` from plain, already-extracted data.

    ``goals`` / ``situations`` are iterables of mappings with an ``id`` (required;
    rows without one are skipped) and optional ``label`` / ``status`` / ``detail``.
    ``extra`` lets a caller add commitments of other kinds directly. Pure: the
    caller does the HA-side extraction, so this stays unit-testable and never
    raises into a capture path.
    """
    clock = now or time.time
    commitments: List[Commitment] = []
    commitments.extend(_commitments_from(GOAL, goals))
    commitments.extend(_commitments_from(SITUATION, situations))
    commitments.extend(c for c in extra if isinstance(c, Commitment))
    return AgencyState(
        captured_ts=float(clock()),
        mode=(str(mode) if mode is not None else None),
        commitments=tuple(commitments),
    )


def continuity_summary(state: Optional[AgencyState], *, now: Optional[Callable[[], float]] = None) -> str:
    """A one-line, log/announce-friendly description of a reloaded snapshot."""
    if state is None:
        return "continuity: no prior agency snapshot"
    clock = now or time.time
    parts: List[str] = []
    if state.mode:
        parts.append(f"mode={state.mode}")
    ng, ns = len(state.goals), len(state.situations)
    if ng:
        parts.append(f"{ng} goal{'s' if ng != 1 else ''}")
    if ns:
        parts.append(f"{ns} open situation{'s' if ns != 1 else ''}")
    if not parts:
        parts.append("nothing in flight")
    age = max(0, int(clock() - state.captured_ts))
    return f"continuity: {' · '.join(parts)} (captured {age}s ago)"


@dataclass(frozen=True)
class ReconcileReport:
    """Which reloaded commitments are still live, and which vanished while down."""

    still_live: Tuple[Commitment, ...] = ()
    vanished: Tuple[Commitment, ...] = ()

    @property
    def resumable(self) -> bool:
        return bool(self.still_live)

    def to_dict(self) -> dict:
        return {
            "still_live": [c.to_dict() for c in self.still_live],
            "vanished": [c.to_dict() for c in self.vanished],
            "live_count": len(self.still_live),
            "vanished_count": len(self.vanished),
        }


def reconcile(
    state: Optional[AgencyState],
    *,
    live_goal_ids: Iterable[str] = (),
    live_situation_ids: Iterable[str] = (),
) -> ReconcileReport:
    """Split a reloaded snapshot's commitments into those still live vs gone.

    A reloaded goal/situation is "still live" only if its id is in the matching
    live-id set the caller passes (goals checked against ``live_goal_ids``,
    situations against ``live_situation_ids``). Commitments of any other kind are
    treated as still live (nothing here can disprove them). Pure and total.
    """
    if state is None:
        return ReconcileReport()
    g_live = {str(x) for x in live_goal_ids}
    s_live = {str(x) for x in live_situation_ids}
    live: List[Commitment] = []
    gone: List[Commitment] = []
    for c in state.commitments:
        if c.kind == GOAL:
            (live if c.id in g_live else gone).append(c)
        elif c.kind == SITUATION:
            (live if c.id in s_live else gone).append(c)
        else:
            live.append(c)
    return ReconcileReport(still_live=tuple(live), vanished=tuple(gone))


class AgencyStore:
    """Durable, append-only store of agency snapshots (newest wins on load).

    Backed by its own DB file through ``kernel.persistence`` (no schema merge).
    Keeps only the newest ``keep`` snapshots so the table can't grow unbounded.
    """

    def __init__(
        self,
        db_path,
        *,
        keep: int = 20,
        now: Optional[Callable[[], float]] = None,
    ) -> None:
        self._db_path = str(db_path)
        self._keep = max(1, int(keep))
        self._now = now or time.time
        persistence.run_migrations(self._db_path, _MIGRATIONS)

    def save(self, state: AgencyState) -> int:
        """Append ``state`` and prune to the newest ``keep``. Returns its row id."""
        with persistence.transaction(self._db_path) as conn:
            cur = conn.execute(
                f"INSERT INTO {_TABLE} (captured_ts, schema_version, payload) "
                "VALUES (?,?,?)",
                (float(state.captured_ts), int(state.schema_version), state.to_json()),
            )
            row_id = int(cur.lastrowid)
            conn.execute(
                f"DELETE FROM {_TABLE} WHERE id NOT IN "
                f"(SELECT id FROM {_TABLE} ORDER BY id DESC LIMIT ?)",
                (self._keep,),
            )
        return row_id

    def load_latest(self) -> Optional[AgencyState]:
        """The most recent snapshot this schema can read, or None.

        A snapshot stamped with a newer ``schema_version`` than we understand is
        skipped (not mis-parsed), falling back to the newest readable one — or
        None. Never raises; a corrupt payload is logged and skipped.
        """
        conn = persistence.connect(self._db_path)
        try:
            rows = conn.execute(
                f"SELECT schema_version, payload FROM {_TABLE} ORDER BY id DESC"
            ).fetchall()
        finally:
            conn.close()
        for schema_version, payload in rows:
            if int(schema_version) > SCHEMA_VERSION:
                continue
            try:
                return AgencyState.from_json(payload)
            except Exception as exc:  # pragma: no cover - defensive
                _LOGGER.debug("agency_state: skipping unreadable snapshot: %s", exc)
                continue
        return None

    def count(self) -> int:
        conn = persistence.connect(self._db_path)
        try:
            return int(conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0])
        finally:
            conn.close()

    def clear(self) -> None:
        with persistence.transaction(self._db_path) as conn:
            conn.execute(f"DELETE FROM {_TABLE}")
