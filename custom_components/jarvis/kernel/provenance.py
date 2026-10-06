"""Provenance — where a piece of state came from (Epistemic Fabric, audit-added).

The 2026-10 audit called for every important piece of state to answer *where did
this come from*: source, when observed, confidence, which model, what corroborates
it, and when it expires. That is essential for learning, debugging, explanations,
security, trust and conflict resolution — a belief with provenance can be defended
or overturned on evidence, a bare value cannot.

This primitive is that wrapper. A :class:`Provenance` pairs a value with its
origin and a validity window; helpers pick the most authoritative fresh record
and merge corroborating records (independent agreement raises confidence via
noisy-OR). It lands **pure**: no Home Assistant import, deterministic, never
raises into a caller. Nothing live records provenance yet (world-model /
situation / recognition attach it in shadow, parity against current reads,
enforce when a consumer reads the provenanced value authoritatively).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, Mapping, Optional, Tuple


def _clamp01(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


@dataclass(frozen=True)
class Provenance:
    """A value plus where it came from and how long it is good for.

    ``value`` is the fact itself (e.g. ``False`` for "garage occupied").
    ``confidence`` is clamped to ``[0, 1]``. ``corroboration`` lists other
    sources that agree. ``expires_ts`` is absolute epoch time; ``None`` = no
    stated expiry (still subject to a caller's own freshness policy).
    """

    value: Any = None
    source: str = ""
    observed_ts: float = 0.0
    confidence: float = 0.0
    model: str = ""
    corroboration: Tuple[str, ...] = ()
    expires_ts: Optional[float] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", _clamp01(self.confidence))

    def is_expired(self, now: Optional[float] = None) -> bool:
        if self.expires_ts is None:
            return False
        clock = now if now is not None else time.time()
        return float(clock) >= float(self.expires_ts)

    def is_fresh(self, now: Optional[float] = None) -> bool:
        return not self.is_expired(now)

    def age(self, now: Optional[float] = None) -> float:
        clock = now if now is not None else time.time()
        return max(0.0, float(clock) - float(self.observed_ts))

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "source": self.source,
            "observed_ts": self.observed_ts,
            "confidence": self.confidence,
            "model": self.model,
            "corroboration": list(self.corroboration),
            "expires_ts": self.expires_ts,
        }

    @classmethod
    def from_dict(cls, d: Mapping) -> "Provenance":
        exp = d.get("expires_ts")
        return cls(
            value=d.get("value"),
            source=str(d.get("source", "") or ""),
            observed_ts=float(d.get("observed_ts", 0.0) or 0.0),
            confidence=_clamp01(d.get("confidence", 0.0)),
            model=str(d.get("model", "") or ""),
            corroboration=tuple(str(c) for c in (d.get("corroboration") or ())),
            expires_ts=(float(exp) if exp is not None else None),
        )


def record(
    value: Any,
    *,
    source: str = "",
    confidence: float = 0.0,
    model: str = "",
    corroboration: Iterable[str] = (),
    now: Optional[Callable[[], float]] = None,
    ttl: Optional[float] = None,
) -> Provenance:
    """Build a :class:`Provenance`, stamping ``observed_ts`` from ``now``.

    ``ttl`` (seconds), when given, sets ``expires_ts = observed_ts + ttl`` so the
    record decays. Pure: the caller supplies the observation; this packages it.
    """
    clock = now or time.time
    ts = float(clock())
    expires = (ts + float(ttl)) if ttl is not None else None
    return Provenance(
        value=value,
        source=str(source or ""),
        observed_ts=ts,
        confidence=_clamp01(confidence),
        model=str(model or ""),
        corroboration=tuple(str(c) for c in corroboration),
        expires_ts=expires,
    )


def select_authoritative(
    records: Iterable[Provenance],
    *,
    now: Optional[float] = None,
) -> Optional[Provenance]:
    """The most authoritative *fresh* record — highest confidence, newest to
    break a tie. Returns None when there are no fresh records. Pure and total."""
    clock = now if now is not None else time.time()
    fresh = [r for r in records if isinstance(r, Provenance) and r.is_fresh(clock)]
    if not fresh:
        return None
    return max(fresh, key=lambda r: (r.confidence, r.observed_ts))


def corroborate(a: Provenance, b: Provenance) -> Provenance:
    """Merge two records of the *same* value into one with combined confidence.

    Independent agreement reinforces via noisy-OR (``1-(1-ca)(1-cb)``); the newer
    observation's timestamp/model lead, and sources merge into ``corroboration``.
    If the values differ this is not corroboration — the higher-confidence record
    is returned unchanged (conflict resolution is a separate concern). Pure.
    """
    if a.value != b.value:
        return a if a.confidence >= b.confidence else b
    newer, older = (a, b) if a.observed_ts >= b.observed_ts else (b, a)
    merged_conf = _clamp01(1.0 - (1.0 - a.confidence) * (1.0 - b.confidence))
    sources: List[str] = []
    for s in (*newer.corroboration, *older.corroboration, newer.source, older.source):
        if s and s not in sources and s != newer.source:
            sources.append(s)
    return Provenance(
        value=newer.value,
        source=newer.source,
        observed_ts=newer.observed_ts,
        confidence=merged_conf,
        model=newer.model,
        corroboration=tuple(sources),
        expires_ts=newer.expires_ts,
    )


def summary(p: Optional[Provenance], *, now: Optional[float] = None) -> str:
    """A one-line, log-friendly description of a record's origin."""
    if p is None:
        return "provenance: none"
    clock = now if now is not None else time.time()
    bits = [f"value={p.value!r}"]
    if p.source:
        bits.append(f"src={p.source}")
    bits.append(f"conf={p.confidence:.2f}")
    if p.model:
        bits.append(f"model={p.model}")
    if p.corroboration:
        bits.append(f"corrob={'+'.join(p.corroboration)}")
    bits.append(f"age={int(p.age(clock))}s")
    if p.expires_ts is not None:
        bits.append("expired" if p.is_expired(clock) else "fresh")
    return "provenance: " + " ".join(bits)
