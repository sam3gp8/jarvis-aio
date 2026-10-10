"""Omnipresent multimodal presence continuity (roadmap Phase AA) — pure primitive.

Unify voice / vision / text / panel / satellites into one coherent presence. The
deep concept is **presence continuity, not a UI layer**: if a conversation starts
on voice and the user walks to another room (voice → mobile → HUD), it is the
*same interaction*. So a :class:`Surface` carries interaction identity, and a
single cross-surface :func:`arbitrate` decides which ONE surface emits a given
utterance — so one notification is announced once, never N times across N
surfaces, and a muted surface is honored everywhere. An :class:`Interaction`
survives a :func:`handoff` between surfaces, keeping its id.

Pure: no Home Assistant import, no I/O, no clock — surfaces are injected and
arbitration/handoff are total, deterministic derivations. A live binder (the
surface registry + the announcement path) wires real surfaces on at the shadow
rung; nothing live consumes it yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

VOICE = "voice"
MOBILE = "mobile"
HUD = "hud"
PANEL = "panel"
SATELLITE = "satellite"
TEXT = "text"
KINDS = (VOICE, MOBILE, HUD, PANEL, SATELLITE, TEXT)

# Default attention preference when no surface is explicitly focused
# (lower = preferred). An on-wall HUD/panel the user is at beats a background
# satellite; a per-surface `priority` override wins over this default.
_RANK = {HUD: 0, PANEL: 1, VOICE: 2, MOBILE: 3, SATELLITE: 4, TEXT: 5}


def _norm_kind(value) -> str:
    v = str(value or "").strip().lower()
    return v if v in KINDS else TEXT


@dataclass(frozen=True)
class Surface:
    """One place JARVIS can speak to / hear from a person. ``focused`` means it
    currently owns the interaction's attention; ``present`` means a person is
    attending it; ``muted`` excludes it from emitting. Values are coerced so a
    malformed surface is still total."""

    surface_id: str
    kind: str = TEXT
    present: bool = False
    muted: bool = False
    focused: bool = False
    priority: Optional[int] = None     # override; lower = preferred
    interaction_id: str = ""

    def __post_init__(self):
        object.__setattr__(self, "surface_id", str(self.surface_id or "").strip())
        object.__setattr__(self, "kind", _norm_kind(self.kind))
        object.__setattr__(self, "present", bool(self.present))
        object.__setattr__(self, "muted", bool(self.muted))
        object.__setattr__(self, "focused", bool(self.focused))
        object.__setattr__(self, "interaction_id", str(self.interaction_id or ""))

    @property
    def eff_priority(self) -> int:
        """Effective attention priority (override, else the kind's default rank)."""
        if self.priority is not None:
            try:
                return int(self.priority)
            except (TypeError, ValueError):
                pass
        return _RANK.get(self.kind, 99)

    def to_dict(self) -> dict:
        return {"surface_id": self.surface_id, "kind": self.kind,
                "present": self.present, "muted": self.muted,
                "focused": self.focused, "eff_priority": self.eff_priority,
                "interaction_id": self.interaction_id}


def _eligible(surfaces) -> List[Surface]:
    return [s for s in (surfaces or [])
            if isinstance(s, Surface) and s.surface_id and s.present and not s.muted]


def arbitrate(surfaces) -> Optional[Surface]:
    """The ONE surface that should emit, so a notification is announced once. A
    muted or not-present surface never wins; a *focused* surface wins over mere
    presence; otherwise the lowest effective priority wins, tie-broken by
    ``surface_id``. Returns ``None`` when nothing is eligible — stay silent rather
    than guess. Total; never raises."""
    elig = _eligible(surfaces)
    if not elig:
        return None
    return sorted(elig, key=lambda s: (0 if s.focused else 1, s.eff_priority, s.surface_id))[0]


def would_double_announce(surfaces) -> bool:
    """True when more than one surface would naively speak — i.e. arbitration is
    doing real work (without it, N surfaces double-announce). Total."""
    return len(_eligible(surfaces)) > 1


def muted_surfaces(surfaces) -> List[str]:
    """Ids of surfaces that are muted — honored everywhere, so a notification is
    suppressed on each. Total."""
    return [s.surface_id for s in (surfaces or [])
            if isinstance(s, Surface) and s.surface_id and s.muted]


@dataclass(frozen=True)
class Interaction:
    """One conversation/notification thread, identified independently of the
    surface carrying it so it can move between surfaces and stay *the same*."""

    interaction_id: str
    surface_id: str
    previous_surface_id: str = ""
    handoffs: int = 0

    def __post_init__(self):
        object.__setattr__(self, "interaction_id", str(self.interaction_id or "").strip())
        object.__setattr__(self, "surface_id", str(self.surface_id or "").strip())
        object.__setattr__(self, "previous_surface_id", str(self.previous_surface_id or ""))
        object.__setattr__(self, "handoffs", max(0, int(self.handoffs or 0)))

    def to_dict(self) -> dict:
        return {"interaction_id": self.interaction_id, "surface_id": self.surface_id,
                "previous_surface_id": self.previous_surface_id, "handoffs": self.handoffs}


def start_interaction(interaction_id: str, surface_id: str) -> Interaction:
    """Begin an interaction on a surface. Pure constructor."""
    return Interaction(interaction_id=interaction_id, surface_id=surface_id)


def handoff(interaction: Interaction, to_surface_id: str) -> Interaction:
    """Move an interaction to a new surface, **preserving its id** — the same
    interaction survives the surface change (voice → mobile → HUD). Returns a NEW
    Interaction; a no-op handoff (same surface) still returns an equivalent value
    without counting a handoff. Total; never raises."""
    if not isinstance(interaction, Interaction):
        return start_interaction("", str(to_surface_id or ""))
    to = str(to_surface_id or "").strip()
    if not to or to == interaction.surface_id:
        return interaction
    return Interaction(
        interaction_id=interaction.interaction_id,
        surface_id=to,
        previous_surface_id=interaction.surface_id,
        handoffs=interaction.handoffs + 1,
    )


def is_same_interaction(a: Optional[Interaction], b: Optional[Interaction]) -> bool:
    """Whether two interaction handles are the same ongoing interaction — the
    continuity test a surface handoff must preserve. Total."""
    return bool(a) and bool(b) and bool(a.interaction_id) and a.interaction_id == b.interaction_id


def summarize(surfaces) -> dict:
    """Compact roll-up for logging/diagnostics. Never raises."""
    ss = [s for s in (surfaces or []) if isinstance(s, Surface)]
    winner = arbitrate(ss)
    return {
        "surfaces": len(ss),
        "present": sum(1 for s in ss if s.present),
        "muted": len(muted_surfaces(ss)),
        "would_double_announce": would_double_announce(ss),
        "emit_on": winner.surface_id if winner else None,
    }
