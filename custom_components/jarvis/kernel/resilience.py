"""Resilient compute federation (roadmap Phase Z) — pure policy primitive.

Graceful degradation and optional *federation* of compute: local-first,
cloud-optional, offline-safe. This is explicitly **NOT "distributed JARVIS."**
The canonical identity, state, authority, agency, world-model and journal MUST
remain under the Home Assistant integration's control; external compute is
disposable. The primitive encodes that boundary **structurally**: a tier that
holds canonical state can never be an *offload* target (the brain stays home),
and the chooser is local-first and fail-safe — an all-unhealthy fleet falls back
to the local/canonical tier rather than raising.

Pure: no Home Assistant import, no I/O, no clock — the tiers and their health are
injected, and every function is a total, deterministic derivation. The live binder
is ``connectivity`` (Phase Z shadow): at each breaker transition (cloud reachable
↔ not) it folds the real breaker health into this model and logs the would-be tier
policy, observe-only — the breaker's own CLOSED/OPEN verdict is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

# Compute tiers, in local-first preference order.
LOCAL = "local"     # on-box / HAOS-local compute (the canonical home of the brain)
EDGE = "edge"       # same-LAN helper — disposable
CLOUD = "cloud"     # off-site — disposable
KINDS = (LOCAL, EDGE, CLOUD)
_RANK = {LOCAL: 0, EDGE: 1, CLOUD: 2}

# Health of a tier.
HEALTHY = "healthy"
DEGRADED = "degraded"   # reachable but slow/impaired
DOWN = "down"
_HEALTH = (HEALTHY, DEGRADED, DOWN)


def _norm(value, allowed, default):
    v = str(value or "").strip().lower()
    return v if v in allowed else default


def _nonneg(x) -> float:
    try:
        f = float(x)
        return f if f >= 0.0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _clamp01(x) -> float:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f < 0.0 else 1.0 if f > 1.0 else f


@dataclass(frozen=True)
class Tier:
    """One compute tier, with its health. ``canonical`` marks a tier that holds
    JARVIS's canonical state — it must stay under the HA integration and can never
    be an offload target. Values are coerced so a malformed tier is still total."""

    name: str
    kind: str = LOCAL
    health: str = HEALTHY
    latency_ms: float = 0.0
    quality: float = 1.0       # 0..1 capability of this tier
    canonical: bool = False

    def __post_init__(self):
        object.__setattr__(self, "name", str(self.name or "").strip())
        object.__setattr__(self, "kind", _norm(self.kind, KINDS, LOCAL))
        object.__setattr__(self, "health", _norm(self.health, _HEALTH, HEALTHY))
        object.__setattr__(self, "latency_ms", _nonneg(self.latency_ms))
        object.__setattr__(self, "quality", _clamp01(self.quality))
        object.__setattr__(self, "canonical", bool(self.canonical))

    @property
    def usable(self) -> bool:
        """Reachable enough to run work (healthy or merely degraded)."""
        return self.health in (HEALTHY, DEGRADED)

    @property
    def is_local(self) -> bool:
        return self.kind == LOCAL

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "health": self.health,
                "latency_ms": self.latency_ms, "quality": self.quality,
                "canonical": self.canonical}


def can_offload(tier: Optional[Tier]) -> bool:
    """Whether work may be offloaded TO this tier. A canonical-state tier never
    can — the canonical brain stays under the HA integration (the HAOS boundary);
    only a disposable EDGE/CLOUD tier is an offload target. Total; never raises."""
    return bool(tier) and (not tier.canonical) and tier.kind in (EDGE, CLOUD)


def _sort_key(t: Tier):
    # Local-first (lower _RANK), then healthy before degraded, then lower latency,
    # then higher quality — deterministic and total.
    return (_RANK.get(t.kind, 99),
            0 if t.health == HEALTHY else 1,
            t.latency_ms,
            -t.quality)


def choose(tiers, *, need_quality: float = 0.0,
           prefer_local: bool = True) -> Optional[Tier]:
    """Pick the tier to run on. Among usable tiers meeting ``need_quality``,
    prefer LOCAL then EDGE then CLOUD (tie-broken by health, then latency, then
    quality). **Fail-safe:** if none is usable, fall back to a canonical or local
    tier if one exists (so the brain keeps running at home even unhealthy), else
    the first tier given, else None. ``prefer_local=False`` ranks purely by
    health/latency/quality (still fail-safe to local). Never raises."""
    ts = [t for t in (tiers or []) if isinstance(t, Tier)]
    if not ts:
        return None
    need = _clamp01(need_quality)
    eligible = [t for t in ts if t.usable and t.quality >= need]
    if eligible:
        if prefer_local:
            return sorted(eligible, key=_sort_key)[0]
        return sorted(eligible, key=lambda t: (0 if t.health == HEALTHY else 1,
                                               t.latency_ms, -t.quality))[0]
    # Fail-safe: keep the brain home even when nothing is "usable".
    for t in ts:
        if t.canonical:
            return t
    for t in ts:
        if t.is_local:
            return t
    return ts[0]


def degrade_order(tiers) -> List[Tier]:
    """The order to try when the current tier goes down: every usable tier,
    local-first then by latency. A canonical/local tier is always included so a
    degradation path always terminates at home. Never raises."""
    ts = [t for t in (tiers or []) if isinstance(t, Tier) and t.usable]
    return sorted(ts, key=_sort_key)


def offload_candidates(tiers, *, min_quality: float = 0.0) -> List[Tier]:
    """Usable, offloadable (disposable EDGE/CLOUD) tiers meeting ``min_quality``,
    best first. Canonical tiers are never candidates. Never raises."""
    need = _clamp01(min_quality)
    ts = [t for t in (tiers or [])
          if isinstance(t, Tier) and t.usable and can_offload(t) and t.quality >= need]
    return sorted(ts, key=_sort_key)


def is_offline_safe(tiers) -> bool:
    """True when JARVIS can keep operating with the network down — i.e. at least
    one usable LOCAL or canonical tier exists. Never raises."""
    return any(isinstance(t, Tier) and t.usable and (t.is_local or t.canonical)
               for t in (tiers or []))


def summarize(tiers) -> dict:
    """A compact roll-up for logging/diagnostics. Never raises."""
    ts = [t for t in (tiers or []) if isinstance(t, Tier)]
    chosen = choose(ts)
    return {
        "tiers": len(ts),
        "usable": sum(1 for t in ts if t.usable),
        "down": sum(1 for t in ts if t.health == DOWN),
        "offline_safe": is_offline_safe(ts),
        "chosen": chosen.name if chosen else None,
        "offload_targets": [t.name for t in offload_candidates(ts)],
    }
