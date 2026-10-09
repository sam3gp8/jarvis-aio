"""Long-horizon agency — durable, resumable, progress-tracked goals (Phase V).

Session-scoped goals vanish on restart; a *long-horizon* goal spans days or
weeks and must survive restarts with its progress intact — the direct payoff of
Phase I (continuity of self). This primitive models such a goal as an ordered set
of **milestones**, each advanceable through a small status lifecycle, with pure
progress derivations (fraction resolved, next milestone, stalled?, complete?)
computed from the record plus the ``now`` the caller passes.

It lands **pure**: a frozen record and total, deterministic transitions
(``advance`` returns a *new* goal) plus roll-ups — no persistence, no clock, no
Home Assistant import, and nothing raises. The durable ledger and restart-resume
wiring arrive later: shadow (a live binder persists these and resumes them from
``agency_state``), parity (resume-after-restart proven against the journal), then
enforce (a multi-day goal drives suggestions), owner-gated behind
``LONG_HORIZON_ENFORCE`` with session-scoped goals as the fail-safe.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Optional, Tuple

# Milestone lifecycle.
PENDING = "pending"   # not started
ACTIVE = "active"     # in progress now
DONE = "done"         # achieved
BLOCKED = "blocked"   # cannot proceed (waiting on something)
SKIPPED = "skipped"   # deliberately abandoned — resolved, but not achieved

_STATUSES = (PENDING, ACTIVE, DONE, BLOCKED, SKIPPED)
# Terminal = resolved: it will not be worked further (achieved or abandoned).
_TERMINAL = frozenset({DONE, SKIPPED})


def _num(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Milestone:
    """One step toward a long-horizon goal, with an honest status and the epoch
    second it last changed. Immutable — transitions return a new Milestone."""

    id: str
    label: str = ""
    status: str = PENDING
    updated_at: float = 0.0

    @property
    def resolved(self) -> bool:
        """True once it will not be worked further (done or skipped)."""
        return self.status in _TERMINAL

    @property
    def achieved(self) -> bool:
        return self.status == DONE

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "status": self.status,
                "updated_at": self.updated_at, "resolved": self.resolved}


@dataclass(frozen=True)
class LongHorizonGoal:
    """A durable, progress-tracked goal: an ordered set of milestones with a
    stable id so it can be persisted and resumed across restarts. Pure — every
    derivation is a function of the fields plus the ``now`` the caller passes."""

    id: str
    title: str = ""
    milestones: Tuple[Milestone, ...] = ()
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def total(self) -> int:
        return len(self.milestones)

    @property
    def resolved(self) -> int:
        """Milestones that will not be worked further (done or skipped)."""
        return sum(1 for m in self.milestones if m.resolved)

    @property
    def achieved(self) -> int:
        return sum(1 for m in self.milestones if m.achieved)

    @property
    def progress(self) -> float:
        """Resolved fraction in ``[0, 1]`` (0.0 when there are no milestones)."""
        return (self.resolved / self.total) if self.total else 0.0

    @property
    def is_complete(self) -> bool:
        """Every milestone resolved (and there is at least one)."""
        return self.total > 0 and all(m.resolved for m in self.milestones)

    def next_milestone(self) -> Optional[Milestone]:
        """The milestone to work next: the first ACTIVE one, else the first
        unresolved one in order. None when the goal is complete (or empty)."""
        for m in self.milestones:
            if m.status == ACTIVE:
                return m
        for m in self.milestones:
            if not m.resolved:
                return m
        return None

    def _last_touch(self) -> float:
        return max([self.updated_at] + [m.updated_at for m in self.milestones])

    def is_stalled(self, now: float, *, max_idle: float) -> bool:
        """True when an incomplete goal has seen no change within ``max_idle``
        seconds — a candidate for a nudge. A complete goal is never stalled."""
        if self.is_complete:
            return False
        return (_num(now) - self._last_touch()) >= max(0.0, _num(max_idle))

    def advance(self, milestone_id: str, status: str, now: float) -> "LongHorizonGoal":
        """Return a NEW goal with one milestone's status set (pure — the receiver
        is unchanged). An unknown milestone id or an invalid status is a no-op
        (returns an equivalent goal), so the transition is total."""
        if status not in _STATUSES:
            return self
        key = (milestone_id or "").strip()
        hit = any(m.id == key for m in self.milestones)
        if not hit:
            return self
        clock = _num(now)
        new_ms = tuple(
            replace(m, status=status, updated_at=clock) if m.id == key else m
            for m in self.milestones
        )
        return replace(self, milestones=new_ms, updated_at=clock)

    def to_dict(self, now: Optional[float] = None) -> dict:
        d = {
            "id": self.id,
            "title": self.title,
            "milestones": [m.to_dict() for m in self.milestones],
            "total": self.total,
            "resolved": self.resolved,
            "achieved": self.achieved,
            "progress": round(self.progress, 4),
            "is_complete": self.is_complete,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        nxt = self.next_milestone()
        d["next"] = nxt.to_dict() if nxt is not None else None
        if now is not None:
            d["last_touch"] = self._last_touch()
        return d


def milestone(label: str, *, id: Optional[str] = None, status: str = PENDING,
              now: float = 0.0) -> Milestone:
    """Build a :class:`Milestone`. Pure. ``id`` defaults to a slug of the label."""
    label = str(label or "").strip()
    mid = (id or "").strip() or _slug(label)
    st = status if status in _STATUSES else PENDING
    return Milestone(id=mid, label=label, status=st, updated_at=_num(now))


def plan_goal(title: str, milestones: Iterable, *, id: str,
              now: float = 0.0) -> LongHorizonGoal:
    """Build a :class:`LongHorizonGoal` from a title and an ordered list of
    milestones (``Milestone`` objects, ``{label,status,id}`` mappings, or bare
    label strings). Pure and total — malformed entries are dropped, ids are made
    unique in order, and ``id`` is the caller's stable goal id."""
    out: list[Milestone] = []
    seen: set[str] = set()
    for m in (milestones or ()):
        made = _coerce_milestone(m, now)
        if made is None:
            continue
        mid = made.id or _slug(made.label) or f"m{len(out) + 1}"
        base = mid
        n = 2
        while mid in seen:            # keep ids unique within the goal
            mid = f"{base}-{n}"
            n += 1
        seen.add(mid)
        out.append(replace(made, id=mid))
    clock = _num(now)
    return LongHorizonGoal(id=str(id).strip(), title=str(title or "").strip(),
                           milestones=tuple(out), created_at=clock, updated_at=clock)


@dataclass(frozen=True)
class LongHorizonStats:
    """A roll-up over a set of long-horizon goals at one instant (shadow logging)."""

    count: int = 0
    complete: int = 0
    stalled: int = 0
    avg_progress: float = 0.0

    def to_dict(self) -> dict:
        return {"count": self.count, "complete": self.complete,
                "stalled": self.stalled, "avg_progress": round(self.avg_progress, 4)}


def summarize(goals: Iterable[LongHorizonGoal], now: float, *,
              max_idle: float = 86400.0) -> LongHorizonStats:
    """Count a set of goals by state at ``now``. Pure and total — a non-goal item
    is skipped rather than raising. ``max_idle`` sets the stalled threshold."""
    count = complete = stalled = 0
    total_progress = 0.0
    for g in goals or ():
        if not isinstance(g, LongHorizonGoal):
            continue
        count += 1
        total_progress += g.progress
        if g.is_complete:
            complete += 1
        elif g.is_stalled(now, max_idle=max_idle):
            stalled += 1
    avg = (total_progress / count) if count else 0.0
    return LongHorizonStats(count=count, complete=complete, stalled=stalled,
                            avg_progress=avg)


def _slug(text: str) -> str:
    out = []
    for ch in (text or "").lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " -_" and out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


def _coerce_milestone(x, now: float) -> Optional[Milestone]:
    if isinstance(x, Milestone):
        return x
    if isinstance(x, str) and x.strip():
        return milestone(x, now=now)
    try:
        # mapping-like
        label = str(x.get("label", "") or "").strip()
        if not label:
            return None
        return milestone(label, id=x.get("id"), status=str(x.get("status", PENDING)),
                         now=now)
    except AttributeError:
        return None
