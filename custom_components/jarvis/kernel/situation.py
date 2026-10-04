"""Situation manager (kernel Phase 3, docs/KERNEL_PLAN.md).

Generalises the ad-hoc intrusion state machine (normal → investigating →
confirmed/false-alarm, scattered across ``intrusion.py`` and the SafetyManager)
into one durable, correlated **situation** lifecycle that any flow — intrusion
first, delivery and hazards later — can drive:

    normal → possible → investigating → confirmed ┐
                     └──────────────→ benign ─────┼→ resolved
                                   confirmed → response ┘

The transition table is pure and fully tested; the manager persists each
situation (with its transition history) through the ``kernel.persistence`` seam
so an open situation survives a restart. Situations are low-frequency, so writes
are synchronous — event-loop callers run manager methods via an executor.

Phase 3 ships the machine additively. ``intrusion`` adopts it as the first
consumer, running alongside the existing path (shadow) until recorded verdicts
match; nothing here is authoritative yet.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any, Dict, List, Optional, Tuple

from . import persistence

_LOGGER = logging.getLogger(__name__)

# ── lifecycle states ────────────────────────────────────────────────────────────
NORMAL = "normal"
POSSIBLE = "possible"
INVESTIGATING = "investigating"
CONFIRMED = "confirmed"
BENIGN = "benign"
RESPONSE = "response"
RESOLVED = "resolved"

ALL_STATES = frozenset(
    {NORMAL, POSSIBLE, INVESTIGATING, CONFIRMED, BENIGN, RESPONSE, RESOLVED})

# Allowed transitions. RESOLVED is terminal; every active state can reach it so a
# situation is never stuck. BENIGN may settle to RESOLVED or relax to NORMAL.
TRANSITIONS: Dict[str, frozenset] = {
    NORMAL: frozenset({POSSIBLE}),
    POSSIBLE: frozenset({INVESTIGATING, BENIGN, NORMAL, RESOLVED}),
    INVESTIGATING: frozenset({CONFIRMED, BENIGN, RESOLVED}),
    CONFIRMED: frozenset({RESPONSE, BENIGN, RESOLVED}),
    RESPONSE: frozenset({RESOLVED}),
    BENIGN: frozenset({RESOLVED, NORMAL}),
    RESOLVED: frozenset(),
}

# States in which a situation is "open" (actively tracked).
ACTIVE_STATES = frozenset({POSSIBLE, INVESTIGATING, CONFIRMED, RESPONSE, BENIGN})
TERMINAL_STATES = frozenset({RESOLVED})


def can_transition(src: str, dst: str) -> bool:
    """True if ``src → dst`` is an allowed lifecycle transition."""
    return dst in TRANSITIONS.get(src, frozenset())


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


# ── hazard verdicts (MCU Phase R, R2) ───────────────────────────────────────────
# Freeze verdict labels — the kernel's pure threshold classification of an outdoor
# temperature, which the re-architecture moves out of the live SafetyManager so
# the hazard situation genuinely *owns* the decision (today it only records it).
FREEZE_CRITICAL = "critical"
FREEZE_WARNING = "warning"
FREEZE_CLEAR = "clear"
FREEZE_NONE = "none"


def freeze_verdict(temp_f: Optional[float], *, warn_f: float, critical_f: float,
                   clear_margin_f: float = 5.0) -> str:
    """Classify an outdoor temperature (°F) for pipe-freeze risk — pure, no state.

    Mirrors the live SafetyManager threshold logic exactly:
      * ``temp_f <= critical_f``              → ``critical`` (act immediately),
      * ``critical_f < temp_f <= warn_f``     → ``warning``  (pipe concern),
      * ``temp_f > warn_f + clear_margin_f``  → ``clear``    (recovered, w/ hysteresis),
      * otherwise                             → ``none``     (in the dead band).
    ``temp_f is None`` (no reading) → ``none``. Deterministic and unit-agnostic:
    the caller converts to °F first. This is the verdict; the hysteresis flag and
    the 1-hour alert cooldown stay in the caller (they are orchestration, not the
    classification)."""
    if temp_f is None:
        return FREEZE_NONE
    if temp_f <= critical_f:
        return FREEZE_CRITICAL
    if temp_f <= warn_f:
        return FREEZE_WARNING
    if temp_f > warn_f + clear_margin_f:
        return FREEZE_CLEAR
    return FREEZE_NONE


def intrusion_gate(*, away: bool, qualifying_motion: bool,
                   require_corroboration: bool, alarm_armed: bool,
                   open_entry: bool, residents_tracked_home: bool = False,
                   sleeping: bool = False) -> bool:
    """Should a *possible-intrusion investigation* open for this motion? Pure.

    Mirrors the entry precondition in the live SafetyManager intrusion path — the
    false-alarm-critical decision that raises the initial "possible intrusion —
    investigating" alert. It opens only when:
      * **no awake resident is positively tracked home while the alarm is not
        armed** — an awake, tracked-home resident with the alarm unarmed means the
        motion is *them*, not an intruder, so the gate never opens even if
        confinement is engaged by a degraded hold (e.g. a lockdown held open
        because the alarm panel is ``unavailable``). This veto is the fix for the
        real-world false alarms where a resident on camera at home was investigated
        as an intruder. The veto deliberately does **not** apply when the alarm is
        explicitly **armed** (``armed_home``/``armed_night``/``armed_away`` — the
        user opted in to monitoring while home) nor at night (``sleeping``), so a
        genuine break-in is still monitored in both cases.
      * presence is **away** (or confinement is engaged), AND
      * there is **qualifying motion**, AND
      * when corroboration is required, there is an **armed alarm or an open
        entry point** (so a lone curtain-flutter never concludes an intrusion).

    Deterministic and side-effect-free. The stateful parts of the investigation
    (cooldown, call-off, resident-on-camera, zone-spread escalation) stay in the
    caller — this is only the gate that decides whether to begin."""
    if residents_tracked_home and not sleeping and not alarm_armed:
        return False
    if not away or not qualifying_motion:
        return False
    if require_corroboration and not (alarm_armed or open_entry):
        return False
    return True


class InvalidTransition(ValueError):
    """Raised when a situation is asked to make a disallowed transition."""


def _new_id() -> str:
    return "sit_" + uuid.uuid4().hex


def _now() -> float:
    return time.time()


@dataclass(frozen=True)
class Situation:
    """One tracked situation and its immutable transition history.

    Frozen: a transition returns a new instance (via :meth:`stepped`) so history
    is append-only and a situation value is safe to share.
    """

    kind: str
    state: str = POSSIBLE
    subject: Optional[str] = None
    location: Optional[str] = None
    correlation_id: Optional[str] = None
    data: dict = field(default_factory=dict)
    history: Tuple[dict, ...] = ()
    id: str = field(default_factory=_new_id)
    created_ts: float = field(default_factory=_now)
    updated_ts: float = field(default_factory=_now)

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def terminal(self) -> bool:
        return is_terminal(self.state)

    def stepped(self, to_state: str, *, reason: str = "",
                event_id: Optional[str] = None, ts: Optional[float] = None) -> "Situation":
        """Return a copy transitioned to ``to_state``, recording the step.

        Raises :class:`InvalidTransition` if the move isn't allowed.
        """
        if not can_transition(self.state, to_state):
            raise InvalidTransition(
                f"{self.kind}: {self.state} → {to_state} is not allowed")
        at = _now() if ts is None else float(ts)
        step = {"from": self.state, "to": to_state, "ts": at,
                "reason": reason or "", "event_id": event_id}
        return replace(self, state=to_state, updated_ts=at,
                       history=self.history + (step,))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["history"] = list(self.history)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Situation":
        allowed = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in d.items() if k in allowed}
        if kwargs.get("history") is not None:
            kwargs["history"] = tuple(kwargs["history"])
        if kwargs.get("data") is None:
            kwargs["data"] = {}
        return cls(**kwargs)


_MIGRATIONS = [
    lambda conn: conn.execute(
        "CREATE TABLE IF NOT EXISTS situations ("
        " id TEXT PRIMARY KEY,"
        " kind TEXT NOT NULL,"
        " state TEXT NOT NULL,"
        " subject TEXT,"
        " location TEXT,"
        " correlation_id TEXT,"
        " data TEXT NOT NULL DEFAULT '{}',"
        " history TEXT NOT NULL DEFAULT '[]',"
        " created_ts REAL NOT NULL,"
        " updated_ts REAL NOT NULL)"
    ),
    lambda conn: conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sit_kind_state ON situations (kind, state)"),
    lambda conn: conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sit_corr ON situations (correlation_id)"),
]


class SituationManager:
    """Durable store + lifecycle driver for situations.

    Synchronous SQLite (via ``kernel.persistence``); call from the event loop
    through an executor. Best-effort on read paths (returns empty/None on error);
    writes raise :class:`InvalidTransition` on a bad transition so a caller bug is
    visible rather than silently dropped.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._schema_ready = False

    def _ensure_schema(self) -> None:
        if not self._schema_ready:
            persistence.run_migrations(self._db_path, _MIGRATIONS)
            self._schema_ready = True

    def open(
        self,
        kind: str,
        *,
        subject: Optional[str] = None,
        location: Optional[str] = None,
        correlation_id: Optional[str] = None,
        data: Optional[dict] = None,
        state: str = POSSIBLE,
    ) -> Situation:
        """Create and persist a new situation (default initial state POSSIBLE)."""
        if state not in ALL_STATES:
            raise ValueError(f"unknown state: {state}")
        sit = Situation(
            kind=kind, state=state, subject=subject, location=location,
            correlation_id=correlation_id, data=dict(data or {}),
            history=({"from": None, "to": state, "ts": _now(),
                      "reason": "opened", "event_id": None},),
        )
        self._upsert(sit)
        return sit

    def transition(
        self,
        situation_id: str,
        to_state: str,
        *,
        reason: str = "",
        event_id: Optional[str] = None,
    ) -> Situation:
        """Transition an open situation, persist it, and return the new value."""
        sit = self.get(situation_id)
        if sit is None:
            raise KeyError(f"no such situation: {situation_id}")
        stepped = sit.stepped(to_state, reason=reason, event_id=event_id)
        self._upsert(stepped)
        return stepped

    def resolve(self, situation_id: str, *, reason: str = "") -> Situation:
        """Convenience: move a situation to RESOLVED."""
        return self.transition(situation_id, RESOLVED, reason=reason)

    def get(self, situation_id: str) -> Optional[Situation]:
        rows = self._query("SELECT * FROM situations WHERE id = ?", (situation_id,))
        return rows[0] if rows else None

    def open_situations(self, kind: Optional[str] = None) -> List[Situation]:
        """Active (non-terminal) situations, newest first, optionally by kind."""
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        params: list = list(ACTIVE_STATES)
        sql = f"SELECT * FROM situations WHERE state IN ({placeholders})"
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY updated_ts DESC"
        return self._query(sql, tuple(params))

    # ── persistence ────────────────────────────────────────────────────────────
    def _upsert(self, sit: Situation) -> None:
        self._ensure_schema()
        with persistence.transaction(self._db_path) as conn:
            conn.execute(
                "INSERT INTO situations "
                "(id, kind, state, subject, location, correlation_id, data, "
                " history, created_ts, updated_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                " state=excluded.state, subject=excluded.subject, "
                " location=excluded.location, correlation_id=excluded.correlation_id, "
                " data=excluded.data, history=excluded.history, "
                " updated_ts=excluded.updated_ts",
                (
                    sit.id, sit.kind, sit.state, sit.subject, sit.location,
                    sit.correlation_id,
                    json.dumps(sit.data, default=str, sort_keys=True),
                    json.dumps(list(sit.history), default=str),
                    sit.created_ts, sit.updated_ts,
                ),
            )

    def _query(self, sql: str, params: tuple) -> List[Situation]:
        try:
            self._ensure_schema()
            conn = persistence.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        except Exception as exc:
            _LOGGER.debug("situation query failed: %s", exc)
            return []
        out = []
        for r in rows:
            d = dict(r)
            for k, default in (("data", {}), ("history", [])):
                try:
                    d[k] = json.loads(d[k]) if d.get(k) else default
                except Exception:
                    d[k] = default
            out.append(Situation.from_dict(d))
        return out
