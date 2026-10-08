"""Spatial & temporal model (kernel primitive — roadmap Phase Q, Embodied JARVIS).

A pure, immutable first-class model of **space** (areas and their adjacency — a
floor-plan graph) and **time** (an hour/weekday resolved to a coarse daypart).
Both are built from plain data — the caller does the HA / floor-plan / clock
extraction — so this stays unit-testable and never touches I/O.

PURE (Phase Q, first rung of the ladder): the structures and their query
surfaces exist and are unit-tested, but nothing in the live integration consumes
them yet. Later rungs wire a space/time view onto ``kernel/world_model.py`` in
shadow, then parity (camera↔sensor mapping, cf. #140), then enforce behind
``SPACE_TIME_ENFORCE`` with the current per-feature mapping (``residence_graph``,
``camera_coverage``, the briefing daypart schedule) as the fail-safe. Nothing
here drives behaviour.

Adjacency rows mirror ``residence_graph.room_adjacency`` exactly, so a builder
can hand its output straight across::

    {area: iterable(neighbor_area)}   # undirected; each side may list the other

Area names are matched case-insensitively (the display name is the first
spelling seen) so "Living Room" and "living room" are one node. Everything is a
frozen dataclass of plain values, hashable and cheap to pass around.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Tuple


def _norm(value) -> str:
    """Display form: stringified and stripped."""
    return str(value if value is not None else "").strip()


def _key(value) -> str:
    """Match form: normalized, lowercased — the identity used to merge areas."""
    return _norm(value).lower()


# ── temporal: coarse dayparts ──────────────────────────────────────────────────
# Canonical, ordered parts of the day. The order is meaningful (cadence /
# before-after comparisons), so keep it chronological from the start of the day.
NIGHT = "night"            # 00:00–04:59
MORNING = "morning"        # 05:00–10:59
MIDDAY = "midday"          # 11:00–13:59
AFTERNOON = "afternoon"    # 14:00–17:59
EVENING = "evening"        # 18:00–21:59
LATE_NIGHT = "late_night"  # 22:00–23:59

DAYPARTS: Tuple[str, ...] = (NIGHT, MORNING, MIDDAY, AFTERNOON, EVENING, LATE_NIGHT)

# Dayparts that count as daylight/active hours (morning through afternoon).
_DAYTIME = frozenset({MORNING, MIDDAY, AFTERNOON})


def _hour(value) -> int:
    """Normalize an hour to 0–23, or -1 when it is missing / out of range."""
    try:
        h = int(value)
    except (TypeError, ValueError):
        return -1
    return h if 0 <= h <= 23 else -1


def daypart_of(hour) -> str:
    """Coarse part-of-day for an hour (0–23). ``""`` when the hour is unknown.

    Boundaries mirror the integration's existing daypart sense (cf.
    ``awareness._daypart_phrase`` and the morning/evening briefing schedule)."""
    h = _hour(hour)
    if h < 0:
        return ""
    if h < 5:
        return NIGHT
    if h < 11:
        return MORNING
    if h < 14:
        return MIDDAY
    if h < 18:
        return AFTERNOON
    if h < 22:
        return EVENING
    return LATE_NIGHT


@dataclass(frozen=True)
class TemporalFrame:
    """When it is, coarsely: an hour resolved to a daypart, with the weekday.

    Build it with :meth:`at`; never mutate. ``hour`` is -1 and ``daypart`` ""
    when the hour was unknown; ``weekday`` is -1 (unknown) or 0=Mon … 6=Sun."""

    hour: int = -1
    weekday: int = -1
    daypart: str = ""

    @classmethod
    def at(cls, hour, weekday=None) -> "TemporalFrame":
        """A frame for a given hour (0–23) and optional weekday (0=Mon … 6=Sun).

        Pure and total — garbage in yields an "unknown" frame, never a raise."""
        h = _hour(hour)
        try:
            wd = int(weekday)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            wd = -1
        if not 0 <= wd <= 6:
            wd = -1
        return cls(hour=h, weekday=wd, daypart=daypart_of(h))

    @property
    def is_known(self) -> bool:
        return self.hour >= 0

    @property
    def is_weekend(self) -> bool:
        """True only when the weekday is known and is Saturday or Sunday."""
        return self.weekday in (5, 6)

    @property
    def is_daytime(self) -> bool:
        """True for morning / midday / afternoon (daylight, active hours)."""
        return self.daypart in _DAYTIME


@dataclass(frozen=True)
class Area:
    """A node: a named area (room / zone), optionally on a floor."""

    name: str
    floor: str = ""


@dataclass(frozen=True)
class SpatialGraph:
    """An immutable floor-plan graph: named areas and undirected adjacency, with
    a small query surface (neighbors / adjacency / BFS distance). Build it with
    :meth:`from_adjacency`; never mutate — derive a new one.

    Edges are stored once, each as a pair of display names ordered by match-key,
    so the graph is hashable and de-duplicated. Home floor plans are small, so
    the queries scan edges directly (same approach as ``kernel.graph``)."""

    areas: Tuple[Area, ...] = ()
    edges: Tuple[Tuple[str, str], ...] = ()

    # ── construction ─────────────────────────────────────────────────────────
    @classmethod
    def from_adjacency(
        cls,
        adjacency: Mapping = None,
        floors: Mapping = None,
    ) -> "SpatialGraph":
        """Build a graph from an adjacency map ``{area: iterable(neighbor)}``.

        The map is treated as undirected: an edge is added whether one side or
        both list the other. Self-loops, blank names and duplicate edges are
        dropped. ``floors`` optionally maps an area name → its floor label. Pure
        and total — never raises on messy input."""
        names: Dict[str, str] = {}        # match-key -> display name (first wins)
        floor_by: Dict[str, str] = {}     # match-key -> floor label

        def _see(raw) -> str:
            disp = _norm(raw)
            k = disp.lower()
            if k and k not in names:
                names[k] = disp
            return k

        # Seed floor labels first so an area that only appears in `floors` (no
        # edges) still becomes a node with its floor.
        for raw_area, raw_floor in (floors or {}).items():
            k = _see(raw_area)
            fl = _norm(raw_floor)
            if k and fl and k not in floor_by:
                floor_by[k] = fl

        edges: List[Tuple[str, str]] = []
        seen_edges = set()
        for raw_area, neighbors in (adjacency or {}).items():
            ak = _see(raw_area)
            if not ak:
                continue
            if isinstance(neighbors, (str, bytes, Mapping)):
                neighbors = (neighbors,)
            for raw_neighbor in neighbors or ():
                nk = _see(raw_neighbor)
                if not nk or nk == ak:
                    continue
                lo, hi = sorted((ak, nk))
                if (lo, hi) in seen_edges:
                    continue
                seen_edges.add((lo, hi))
                edges.append((names[lo], names[hi]))

        areas = tuple(
            Area(name=names[k], floor=floor_by.get(k, ""))
            for k in sorted(names)
        )
        return cls(areas=areas, edges=tuple(edges))

    # ── queries (all pure, case-insensitive) ──────────────────────────────────
    def area(self, name: str) -> Optional[Area]:
        """The area node with this name (case-insensitive), or None."""
        k = _key(name)
        for a in self.areas:
            if _key(a.name) == k:
                return a
        return None

    def neighbors(self, name: str) -> List[str]:
        """Display names of areas directly adjacent to ``name`` (case-insensitive),
        sorted by match-key for determinism. Empty for an unknown area."""
        k = _key(name)
        out: List[str] = []
        seen = set()
        for a, b in self.edges:
            other = None
            if _key(a) == k:
                other = b
            elif _key(b) == k:
                other = a
            if other is not None:
                ok = _key(other)
                if ok not in seen:
                    seen.add(ok)
                    out.append(other)
        return sorted(out, key=_key)

    def adjacent(self, a: str, b: str) -> bool:
        """True when areas ``a`` and ``b`` share an edge (case-insensitive)."""
        ka, kb = _key(a), _key(b)
        for x, y in self.edges:
            kx, ky = _key(x), _key(y)
            if (kx == ka and ky == kb) or (kx == kb and ky == ka):
                return True
        return False

    def hops_from(self, origin: str) -> Dict[str, int]:
        """BFS distance in rooms of every reachable area from ``origin``, keyed
        by display name: ``{origin: 0, neighbor: 1, ...}``. Unreachable areas are
        omitted; an unknown origin yields ``{}``. (Pure counterpart of
        ``residence_graph.hops_from_breach``.)"""
        start = self.area(origin)
        if start is None:
            return {}
        depth: Dict[str, int] = {start.name: 0}
        frontier: deque = deque([start.name])
        while frontier:
            here = frontier.popleft()
            for nxt in self.neighbors(here):
                if nxt not in depth:
                    depth[nxt] = depth[here] + 1
                    frontier.append(nxt)
        return depth

    def distance(self, a: str, b: str) -> Optional[int]:
        """Number of room-hops between ``a`` and ``b`` (0 if the same area), or
        None when either is unknown or they are not connected."""
        if self.area(a) is None or self.area(b) is None:
            return None
        kb = _key(b)
        for name, d in self.hops_from(a).items():
            if _key(name) == kb:
                return d
        return None

    def within(self, origin: str, hops: int) -> List[str]:
        """Display names of areas reachable within ``hops`` of ``origin``
        (excluding ``origin`` itself), nearest first then by match-key. Empty for
        an unknown origin or ``hops`` < 1."""
        if hops < 1:
            return []
        reached = [
            (d, name)
            for name, d in self.hops_from(origin).items()
            if 0 < d <= hops
        ]
        reached.sort(key=lambda item: (item[0], _key(item[1])))
        return [name for _, name in reached]

    def is_empty(self) -> bool:
        return not self.areas and not self.edges
