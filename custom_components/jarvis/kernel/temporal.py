"""Temporal validity — valid-as-of / expires-at (Epistemic Fabric, audit-added).

The fifth Epistemic-Fabric primitive. Perception and knowledge are not just
*uncertain* (``kernel.uncertainty``) and *sourced* (``kernel.provenance``) — they
are also **time-bound**. "The garage was empty 30 minutes ago" is a different
claim from "the garage is empty", and "the door was unlocked" expires faster than
"Sam's coffee is oat milk". This primitive makes that first-class: a value's
``observed_at`` (valid-as-of) and an optional ``ttl`` (→ ``expires_at``), with a
coarse freshness **band** (fresh / aging / expired / durable) a consumer can
reason over — "probably still true", "getting stale", "expired, re-check".

It lands **pure**: a record, deterministic derivations (age / remaining /
is_valid / fraction-elapsed / band / freshness) and a roll-up, with ``now`` always
passed in so nothing here reads the clock or raises. No Home Assistant import.
Provenance already carries observed-at + a cache ttl; this generalises that into
a reusable notion the world-model, beliefs and provenance can all share. Ladder
from here: shadow (a live source logs its validity band distribution) → parity →
enforce (a decision defers on a stale/expired value), owner-gated.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

# bands, freshest → most stale
FRESH = "fresh"        # well within its lifetime
AGING = "aging"        # past the halfway point but not yet expired
EXPIRED = "expired"    # past expires_at — should be re-confirmed before use
DURABLE = "durable"    # no ttl — does not expire (e.g. a stated preference)

# fraction-of-lifetime boundary between FRESH and AGING
_FRESH_UNTIL = 0.5


def _num(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Validity:
    """How time-bound a value is: when it was observed and when it expires.

    ``observed_at`` is the epoch second the value was last confirmed (valid-as-of);
    ``ttl`` is its lifetime in seconds, or ``None`` for a value that does not
    expire (``DURABLE`` — e.g. a stated preference). All reasoning is a pure
    function of these plus the ``now`` the caller passes.
    """

    observed_at: float = 0.0
    ttl: Optional[float] = None

    @property
    def durable(self) -> bool:
        """True when the value carries no expiry."""
        return self.ttl is None

    @property
    def expires_at(self) -> Optional[float]:
        """Epoch second the value expires, or ``None`` when durable."""
        if self.ttl is None:
            return None
        return self.observed_at + max(0.0, _num(self.ttl))

    def age(self, now: float) -> float:
        """Seconds since the value was observed (never negative)."""
        return max(0.0, _num(now) - self.observed_at)

    def remaining(self, now: float) -> Optional[float]:
        """Seconds until expiry (0 once expired), or ``None`` when durable."""
        if self.ttl is None:
            return None
        return max(0.0, max(0.0, _num(self.ttl)) - self.age(now))

    def is_valid(self, now: float) -> bool:
        """Whether the value is still within its lifetime (always True durable)."""
        if self.ttl is None:
            return True
        return self.age(now) < max(0.0, _num(self.ttl))

    def fraction_elapsed(self, now: float) -> float:
        """Fraction of the lifetime spent, in ``[0, ∞)`` (0.0 when durable or the
        ttl is non-positive). ``>= 1.0`` means expired."""
        ttl = max(0.0, _num(self.ttl)) if self.ttl is not None else 0.0
        if ttl <= 0.0:
            return 0.0
        return self.age(now) / ttl

    def freshness(self, now: float) -> float:
        """A bounded ``[0, 1]`` freshness: 1.0 at observation, decaying linearly to
        0.0 at expiry. Durable values are always 1.0 (they never go stale)."""
        if self.ttl is None:
            return 1.0
        return max(0.0, 1.0 - self.fraction_elapsed(now))

    def band(self, now: float) -> str:
        """Coarse freshness band the fabric exposes."""
        if self.ttl is None:
            return DURABLE
        frac = self.fraction_elapsed(now)
        if frac >= 1.0:
            return EXPIRED
        if frac < _FRESH_UNTIL:
            return FRESH
        return AGING

    def describe(self, now: float) -> str:
        """Honest one-line phrasing of the value's time standing."""
        b = self.band(now)
        if b == DURABLE:
            return "durable (no expiry)"
        age = int(self.age(now))
        if b == EXPIRED:
            return f"expired (observed {age}s ago) — re-confirm"
        rem = self.remaining(now)
        rem_s = int(rem) if rem is not None else 0
        return f"{b} (observed {age}s ago; expires in {rem_s}s)"

    def to_dict(self, now: Optional[float] = None) -> dict:
        d = {"observed_at": self.observed_at, "ttl": self.ttl,
             "expires_at": self.expires_at, "durable": self.durable}
        if now is not None:
            d.update(age=self.age(now), remaining=self.remaining(now),
                     is_valid=self.is_valid(now), band=self.band(now),
                     freshness=round(self.freshness(now), 4))
        return d


def assess(observed_at: float, *, ttl: Optional[float] = None) -> Validity:
    """Build a :class:`Validity`. Pure: the caller supplies when and how long."""
    return Validity(observed_at=_num(observed_at),
                    ttl=(None if ttl is None else max(0.0, _num(ttl))))


@dataclass(frozen=True)
class ValidityStats:
    """A roll-up of a set of validities at one instant (for shadow logging)."""

    count: int = 0
    fresh: int = 0
    aging: int = 0
    expired: int = 0
    durable: int = 0

    @property
    def valid(self) -> int:
        """Items still usable now (everything but expired)."""
        return self.count - self.expired

    def to_dict(self) -> dict:
        return {"count": self.count, "fresh": self.fresh, "aging": self.aging,
                "expired": self.expired, "durable": self.durable,
                "valid": self.valid}


def summarize(items: Iterable[Validity], now: float) -> ValidityStats:
    """Count a set of validities by band at ``now``. Pure and total — a non
    -:class:`Validity` item is skipped rather than raising."""
    fresh = aging = expired = durable = count = 0
    for v in items or ():
        try:
            b = v.band(now)
        except Exception:
            continue
        count += 1
        if b == FRESH:
            fresh += 1
        elif b == AGING:
            aging += 1
        elif b == EXPIRED:
            expired += 1
        elif b == DURABLE:
            durable += 1
    return ValidityStats(count=count, fresh=fresh, aging=aging,
                         expired=expired, durable=durable)
