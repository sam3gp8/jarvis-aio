"""Integration loop trace — the closed cognitive/agency loop as one record.

docs/KERNEL_PLAN.md, **Phase R (Integration Gate)**. R is not a new capability;
it proves the architecture can *operate as one* — that a single real pass runs
the whole way round **perceive → predict → decide → act → learn**, tying
cognition (phases J–M) and agency (N–V) into one correlated,
journal-reconstructable chain rather than five subsystems that happen to share a
process.

This module is the *pure record* of that loop. A :class:`LoopTrace` holds the
canonical stages, each either present (with a short summary and the kernel
primitive that owned it) or absent, under **one correlation id** — the thread
that makes the pass reconstructable from the journal. ``is_closed`` is True only
when every stage is present, i.e. the pass went all the way round; ``reached``
names how far a partial pass got. Most passes are *open* (perceive + decide, no
act) — which is exactly what the trace should show; R's job is to make closure
observable (shadow), then measured over a representative set (parity), then owned
for one real end-to-end scenario (enforce) with a CI gate against open-loop
regressions. Fail-safe up the ladder is the current open-loop behaviour.

Pure: no Home Assistant import, no I/O, no clock baked in (the caller passes
``started_ts``), so the record is deterministic and unit-testable. It lands pure
and additive — the live producer assembles one from a real tick and logs it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# ── the canonical loop, in order ───────────────────────────────────────────────
PERCEIVE = "perceive"   # read the world (world_model)
PREDICT = "predict"     # anticipate what happens next (causal / cognition / attention)
DECIDE = "decide"       # choose an action (plan / authority / autonomy)
ACT = "act"             # perform it (actuation / outcome)
LEARN = "learn"         # fold the outcome back in (outcome / learning)

STAGES: Tuple[str, ...] = (PERCEIVE, PREDICT, DECIDE, ACT, LEARN)
_RANK: Dict[str, int] = {s: i for i, s in enumerate(STAGES)}


@dataclass(frozen=True)
class LoopStage:
    """One stage of a loop pass: whether it happened, a one-line summary, and the
    kernel primitive / module that owned it."""

    stage: str
    present: bool = False
    summary: str = ""
    owner: str = ""

    def to_dict(self) -> dict:
        return {"stage": self.stage, "present": self.present,
                "summary": self.summary, "owner": self.owner}


@dataclass(frozen=True)
class LoopTrace:
    """The reconstructable record of one perceive→predict→decide→act→learn pass.

    ``correlation_id`` is the thread tying the pass's kernel records together in
    the journal. Stages are held in canonical order; a stage absent from
    ``stages`` is treated as not-present."""

    correlation_id: str
    started_ts: float = 0.0
    stages: Tuple[LoopStage, ...] = ()

    def _by(self) -> Dict[str, LoopStage]:
        return {s.stage: s for s in self.stages if isinstance(s, LoopStage)}

    @property
    def present_stages(self) -> Tuple[str, ...]:
        """Canonical-ordered stages that happened on this pass."""
        by = self._by()
        return tuple(s for s in STAGES if by.get(s) is not None and by[s].present)

    @property
    def missing_stages(self) -> Tuple[str, ...]:
        present = set(self.present_stages)
        return tuple(s for s in STAGES if s not in present)

    @property
    def is_closed(self) -> bool:
        """True only when every canonical stage is present — the loop closed."""
        return set(self.present_stages) == set(STAGES)

    @property
    def reached(self) -> str:
        """The furthest stage reached from PERCEIVE without a gap (the leading
        run of present stages). ``""`` when even PERCEIVE is absent — a pass that
        never got off the ground."""
        by = self._by()
        last = ""
        for s in STAGES:
            st = by.get(s)
            if st is not None and st.present:
                last = s
            else:
                break
        return last

    @property
    def depth(self) -> int:
        """How many leading stages ran (0..5)."""
        r = self.reached
        return (_RANK[r] + 1) if r else 0

    def summary(self) -> str:
        """One-line, deterministic headline for the shadow log."""
        mark = "".join(("+" if s in self.present_stages else "-") for s in STAGES)
        state = "CLOSED" if self.is_closed else f"open@{self.reached or 'none'}"
        return f"[{mark}] {state} {self.depth}/{len(STAGES)} corr={self.correlation_id or '-'}"

    def to_dict(self) -> dict:
        by = self._by()
        return {
            "correlation_id": self.correlation_id,
            "started_ts": self.started_ts,
            "is_closed": self.is_closed,
            "reached": self.reached,
            "depth": self.depth,
            "present": list(self.present_stages),
            "missing": list(self.missing_stages),
            "stages": [by[s].to_dict() for s in STAGES if s in by],
        }


def stage(name: str, present: bool = False, *, summary: str = "",
          owner: str = "") -> LoopStage:
    """Build a :class:`LoopStage`, normalising ``name`` to a canonical stage."""
    nm = str(name or "").strip().lower()
    return LoopStage(stage=nm, present=bool(present), summary=str(summary or ""),
                     owner=str(owner or ""))


def trace(correlation_id: str, stages: Optional[Tuple] = None, *,
          started_ts: float = 0.0) -> LoopTrace:
    """Build a :class:`LoopTrace` from an iterable of :class:`LoopStage` (or
    ``(name, present, summary, owner)`` tuples). Order-independent input; the
    trace keeps canonical order. Pure and total."""
    out = []
    for s in (stages or ()):
        if isinstance(s, LoopStage):
            out.append(s)
        elif isinstance(s, (tuple, list)) and s:
            out.append(stage(*s))
    # keep canonical order, last-wins per stage
    by = {st.stage: st for st in out}
    ordered = tuple(by[s] for s in STAGES if s in by)
    return LoopTrace(correlation_id=str(correlation_id or ""),
                     started_ts=float(started_ts or 0.0), stages=ordered)


@dataclass
class LoopAccumulator:
    """A rolling tally of loop passes — the closed-loop RATE and where open passes
    stall. Phase R *parity* reads this to quantify closure over real traffic
    before anything gates on it (enforce). It accumulates, so it is not frozen,
    but every read (``rate``, ``reached_histogram``, ``summary``) is a pure
    function of what was recorded."""

    total: int = 0
    closed: int = 0
    _reached: Dict[str, int] = field(default_factory=dict)

    def record(self, trace: "LoopTrace") -> None:
        """Fold one pass into the tally. Ignores anything that is not a
        :class:`LoopTrace` (defensive)."""
        if not isinstance(trace, LoopTrace):
            return
        self.total += 1
        if trace.is_closed:
            self.closed += 1
        key = trace.reached or "none"
        self._reached[key] = self._reached.get(key, 0) + 1

    @property
    def rate(self) -> float:
        """Fraction of recorded passes that closed the loop (0.0 when none)."""
        return (self.closed / self.total) if self.total else 0.0

    def reached_histogram(self) -> Dict[str, int]:
        """How far passes got, in canonical order (``none`` first for passes that
        did not even perceive)."""
        order = ("none",) + STAGES
        return {s: self._reached[s] for s in order if s in self._reached}

    def summary(self) -> str:
        """One-line dashboard: closed-loop rate + the stall distribution."""
        if not self.total:
            return "no passes recorded"
        hist = " ".join(f"{s}:{n}" for s, n in self.reached_histogram().items())
        return (f"closed {self.closed}/{self.total} ({self.rate * 100:.0f}%); "
                f"reached [{hist}]")

    def to_dict(self) -> dict:
        return {"total": self.total, "closed": self.closed,
                "rate": round(self.rate, 4),
                "reached": self.reached_histogram()}

    def reset(self) -> None:
        self.total = 0
        self.closed = 0
        self._reached = {}


def from_flags(correlation_id: str, *, started_ts: float = 0.0,
               perceive: bool = False, predict: bool = False,
               decide: bool = False, act: bool = False, learn: bool = False,
               summaries: Optional[Dict[str, str]] = None,
               owners: Optional[Dict[str, str]] = None) -> LoopTrace:
    """Convenience builder from one boolean per canonical stage, with optional
    per-stage summary/owner. The single entry point the live shadow producer
    uses, so the trace shape stays in one place. Pure and total."""
    summaries = summaries or {}
    owners = owners or {}
    flags = {PERCEIVE: perceive, PREDICT: predict, DECIDE: decide,
             ACT: act, LEARN: learn}
    # Default owners — the kernel primitive that canonically owns each stage.
    default_owner = {
        PERCEIVE: "world_model", PREDICT: "causal", DECIDE: "plan",
        ACT: "actuation", LEARN: "outcome",
    }
    built = [
        stage(s, flags[s], summary=summaries.get(s, ""),
              owner=owners.get(s, default_owner[s]))
        for s in STAGES
    ]
    return trace(correlation_id, tuple(built), started_ts=started_ts)
