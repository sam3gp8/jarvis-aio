"""Working memory — a bounded, decay-scored cognitive context (kernel Phase K).

docs/KERNEL_PLAN.md, Phase K ("Attention & Working Memory"). Today the context
that drives a decision is assembled ad-hoc by whichever path happens to be
running — the context assembler, `cognitive_core`, a proactive heuristic — each
building its own hidden working set. This primitive is the one canonical,
**capacity-bounded** working set: the small, decaying set of things JARVIS is
currently holding in mind, ranked so the most salient survive and the stale fall
out deterministically.

The audit's sharpening is the whole point: working memory must represent the
*current cognitive context*, not merely "salient events". So items are typed
against a canonical set of cognitive-context kinds — the **current situation**,
the **active objective / intent**, the **people** and **devices** in play,
**recent observations**, **unresolved questions**, **pending actions** and their
**verification**, **important memories**, **predictions** and **constraints** —
and a :class:`WorkingSnapshot` renders a bounded view grouped by kind, which is
what a decision engine (Phase K's parity/enforce rungs, and Phase J's cognitive
cycle) will read instead of assembling its own.

Pure: no Home Assistant import, no I/O, no clock dependency baked in (the caller
passes ``now`` so scoring is deterministic and unit-testable). It lands pure and
additive — nothing populates or reads it live yet. The ladder from here is
shadow (populate from the bus / cycle), parity (attention consults it), enforce
(arbitration reads it authoritatively), behind ``WORKING_MEMORY_ENFORCE`` with a
fail-safe to the current attention inputs.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

# ── canonical cognitive-context kinds ────────────────────────────────────────
# The audit's list of what the current cognitive context *is*. Ordered for a
# stable, meaningful snapshot layout (what matters most to a decision first).
KIND_SITUATION = "situation"        # the current situation / machine state
KIND_OBJECTIVE = "objective"        # the active objective / goal
KIND_INTENT = "intent"              # the current intent (what JARVIS means to do)
KIND_PERSON = "person"              # the people in play
KIND_DEVICE = "device"              # the devices in play
KIND_OBSERVATION = "observation"    # recent observations
KIND_QUESTION = "question"          # unresolved questions
KIND_ACTION = "action"              # pending actions
KIND_VERIFICATION = "verification"  # pending verification of an action
KIND_MEMORY = "memory"              # important memories surfaced into context
KIND_PREDICTION = "prediction"      # predictions about what happens next
KIND_CONSTRAINT = "constraint"      # constraints that bound the decision

# Canonical order (also the snapshot's section order).
KINDS: Tuple[str, ...] = (
    KIND_SITUATION, KIND_OBJECTIVE, KIND_INTENT, KIND_PERSON, KIND_DEVICE,
    KIND_OBSERVATION, KIND_QUESTION, KIND_ACTION, KIND_VERIFICATION,
    KIND_MEMORY, KIND_PREDICTION, KIND_CONSTRAINT,
)
_KIND_RANK: Dict[str, int] = {k: i for i, k in enumerate(KINDS)}

DEFAULT_CAPACITY = 32          # items held before eviction (bounded working set)
DEFAULT_HALF_LIFE = 300.0      # seconds for a non-pinned item's salience to halve
_MIN_HALF_LIFE = 1e-6          # guard against a zero/negative half-life


def _clamp01(x, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


@dataclass(frozen=True)
class WorkingItem:
    """One thing currently held in mind.

    ``kind`` is one of the canonical cognitive-context kinds; ``subject`` is the
    stable identity of the thing (dedup key within a kind — a new read of the
    same subject refreshes rather than duplicates); ``content`` is the human
    detail. ``salience`` (``0..1``) is the item's *base* importance before time
    decay; ``pinned`` items neither decay nor get evicted (an active objective, a
    live constraint). ``ts`` is when it was last observed; ``correlation_id`` and
    ``source`` tie it back into the kernel chain.
    """

    kind: str
    subject: str
    content: str = ""
    salience: float = 0.5
    pinned: bool = False
    ts: float = 0.0
    correlation_id: str = ""
    source: str = ""

    @property
    def key(self) -> Tuple[str, str]:
        return (self.kind, self.subject)

    def score(self, now: float, half_life: float = DEFAULT_HALF_LIFE) -> float:
        """Current salience after recency decay.

        Pinned items hold their base salience (they are, by definition, kept in
        mind). Everything else decays with an exponential half-life on its age,
        so a fresh low-salience note can briefly outrank a stale important one —
        which is what a working set should do.
        """
        base = _clamp01(self.salience)
        if self.pinned:
            return base
        hl = half_life if half_life and half_life > _MIN_HALF_LIFE else _MIN_HALF_LIFE
        age = now - self.ts
        if age <= 0.0:
            return base
        return base * (0.5 ** (age / hl))

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "subject": self.subject, "content": self.content,
            "salience": round(_clamp01(self.salience), 4), "pinned": self.pinned,
            "ts": self.ts, "correlation_id": self.correlation_id,
            "source": self.source,
        }


@dataclass(frozen=True)
class WorkingSnapshot:
    """A bounded view of the current cognitive context.

    ``by_kind`` maps each present canonical kind to its items (most salient
    first); ``salient`` is the flat top-N across all kinds. This is the canonical
    object a decision engine reads — the single answer to "what is JARVIS holding
    in mind right now?" — instead of each path assembling its own working set.
    """

    now: float
    by_kind: Dict[str, Tuple[WorkingItem, ...]] = field(default_factory=dict)
    salient: Tuple[WorkingItem, ...] = ()
    total: int = 0
    capacity: int = DEFAULT_CAPACITY

    @property
    def is_empty(self) -> bool:
        return self.total == 0

    def kinds(self) -> Tuple[str, ...]:
        """Present kinds, in canonical order."""
        return tuple(k for k in KINDS if k in self.by_kind)

    def summary(self) -> str:
        """One-line, deterministic headline (per-kind counts, then top subjects)."""
        if self.is_empty:
            return "working memory empty"
        parts = [f"{k}×{len(self.by_kind[k])}" for k in self.kinds()]
        top = ", ".join(f"{it.kind}:{it.subject}" for it in self.salient[:3])
        return f"{self.total}/{self.capacity} held [{' '.join(parts)}]" + (
            f" — top {top}" if top else "")

    def to_dict(self) -> dict:
        return {
            "now": self.now, "total": self.total, "capacity": self.capacity,
            "by_kind": {k: [it.to_dict() for it in v]
                        for k, v in self.by_kind.items()},
            "salient": [it.to_dict() for it in self.salient],
        }


class WorkingMemory:
    """A capacity-bounded, decay-scored working set with deterministic eviction.

    Not frozen — it holds live state — but every decision it makes is a pure
    function of its contents plus the ``now`` the caller passes, so it is fully
    deterministic and testable. Adding the same ``(kind, subject)`` again
    *refreshes* that item (new ts / salience / content) rather than duplicating
    it. When a non-pinned add would exceed capacity, the lowest-scoring evictable
    item is dropped; ties break by oldest ``ts`` then earliest insertion, so the
    eviction is reproducible, never arbitrary. Pinned items are exempt from
    eviction (only ever displaced by other pinned items when pins alone exceed
    capacity).
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY,
                 half_life: float = DEFAULT_HALF_LIFE) -> None:
        self.capacity = max(1, int(capacity))
        self.half_life = (half_life if half_life and half_life > _MIN_HALF_LIFE
                          else DEFAULT_HALF_LIFE)
        self._items: Dict[Tuple[str, str], WorkingItem] = {}
        self._seq: Dict[Tuple[str, str], int] = {}  # insertion order per key
        self._counter: int = 0

    # ── writes ────────────────────────────────────────────────────────────────
    def remember(self, kind: str, subject: str, *, content: str = "",
                 salience: float = 0.5, pinned: bool = False,
                 correlation_id: str = "", source: str = "",
                 now: Optional[float] = None) -> WorkingItem:
        """Add or refresh an item, then enforce the capacity bound.

        A repeat of an existing ``(kind, subject)`` keeps its original insertion
        order (so a steadily-refreshed item is not treated as newest for
        tie-breaks) while taking the new ts / salience / content / pin."""
        now = time.time() if now is None else now
        item = WorkingItem(
            kind=str(kind), subject=str(subject), content=str(content),
            salience=_clamp01(salience, 0.5), pinned=bool(pinned),
            ts=float(now), correlation_id=str(correlation_id), source=str(source))
        key = item.key
        if key not in self._seq:
            self._seq[key] = self._counter
            self._counter += 1
        self._items[key] = item
        self._evict(now)
        return item

    def touch(self, kind: str, subject: str,
              now: Optional[float] = None) -> Optional[WorkingItem]:
        """Refresh an existing item's recency without changing anything else.

        Returns the refreshed item, or ``None`` if it is not held."""
        now = time.time() if now is None else now
        key = (str(kind), str(subject))
        cur = self._items.get(key)
        if cur is None:
            return None
        refreshed = WorkingItem(
            kind=cur.kind, subject=cur.subject, content=cur.content,
            salience=cur.salience, pinned=cur.pinned, ts=float(now),
            correlation_id=cur.correlation_id, source=cur.source)
        self._items[key] = refreshed
        return refreshed

    def forget(self, kind: str, subject: str) -> bool:
        """Drop an item if present; returns whether anything was removed."""
        key = (str(kind), str(subject))
        if key in self._items:
            del self._items[key]
            self._seq.pop(key, None)
            return True
        return False

    def clear(self) -> None:
        self._items.clear()
        self._seq.clear()

    def _evict(self, now: float) -> None:
        """Bring the set back within capacity by dropping the weakest item(s).

        Prefers to evict non-pinned items; only when pinned items alone exceed
        capacity does it drop the weakest pinned one. Ordering is total and
        deterministic: lowest score, then oldest ts, then earliest insertion."""
        while len(self._items) > self.capacity:
            evictable = [k for k, it in self._items.items() if not it.pinned]
            pool = evictable if evictable else list(self._items.keys())
            victim = min(pool, key=lambda k: (
                self._items[k].score(now, self.half_life),
                self._items[k].ts,
                self._seq.get(k, 0),
            ))
            del self._items[victim]
            self._seq.pop(victim, None)

    # ── reads ───────────────────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key) -> bool:
        try:
            return (str(key[0]), str(key[1])) in self._items
        except (TypeError, IndexError):
            return False

    def get(self, kind: str, subject: str) -> Optional[WorkingItem]:
        return self._items.get((str(kind), str(subject)))

    def items(self) -> List[WorkingItem]:
        """All held items (unordered view, as a fresh list)."""
        return list(self._items.values())

    def _ordered(self, now: float,
                 candidates: Iterable[WorkingItem]) -> List[WorkingItem]:
        """Sort by score desc, deterministically: higher score first, then by
        canonical kind, then subject — so equal-score items never shuffle."""
        return sorted(candidates, key=lambda it: (
            -it.score(now, self.half_life),
            _KIND_RANK.get(it.kind, len(KINDS)),
            it.subject,
        ))

    def by_kind(self, kind: str, now: Optional[float] = None) -> List[WorkingItem]:
        """Items of one kind, most salient first."""
        now = time.time() if now is None else now
        kind = str(kind)
        return self._ordered(now, (it for it in self._items.values()
                                   if it.kind == kind))

    def top(self, k: int = 5, now: Optional[float] = None) -> List[WorkingItem]:
        """The ``k`` most salient items across all kinds."""
        now = time.time() if now is None else now
        if k <= 0:
            return []
        return self._ordered(now, self._items.values())[:k]

    def snapshot(self, now: Optional[float] = None, *, per_kind: int = 3,
                 top_k: int = 7) -> WorkingSnapshot:
        """Render the bounded cognitive-context view a decision engine reads.

        ``per_kind`` caps each kind's section; ``top_k`` caps the flat salient
        list. Both default small — a working set is meant to be a handful of
        things, not the whole knowledge base."""
        now = time.time() if now is None else now
        by_kind: Dict[str, Tuple[WorkingItem, ...]] = {}
        for k in KINDS:
            got = self.by_kind(k, now)
            if got:
                by_kind[k] = tuple(got[:max(0, per_kind)])
        salient = tuple(self.top(max(0, top_k), now))
        return WorkingSnapshot(now=now, by_kind=by_kind, salient=salient,
                               total=len(self._items), capacity=self.capacity)


# ── the one canonical working set (Phase K) ──────────────────────────────────
# The audit's done-criterion for Phase K is that "the decision engine receives a
# canonical bounded cognitive context from WorkingMemory" — i.e. ONE shared set,
# not a per-loop one. This process-wide singleton is that set: the producer
# (cognitive_core's tick) writes to it, and consumers (attention arbitration)
# read it. It is still observe-only up the ladder — a reader logs what consulting
# it would change (parity) before anything gates on it (enforce).
_SHARED: Optional[WorkingMemory] = None


def shared() -> WorkingMemory:
    """The process-wide canonical working set, created lazily with defaults."""
    global _SHARED
    if _SHARED is None:
        _SHARED = WorkingMemory()
    return _SHARED


def reset_shared() -> None:
    """Drop the shared working set (used on reload and in tests)."""
    global _SHARED
    _SHARED = None
