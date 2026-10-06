"""Uncertainty — first-class "what do I know vs. think vs. guess" (Epistemic Fabric).

The 2026-10 audit asked for uncertainty to be a first-class architectural
concept: an autonomous system must distinguish *"I believe the garage is empty:
0.92"* from *"the garage is empty."* Those are radically different statements to
act on. This primitive is that distinction — a value paired with a confidence, a
band (**known / believed / guessed / unknown**), what the belief rests on, and
*what evidence would resolve it*.

It lands **pure**: a bounded :class:`Uncertain` value, band classification,
honest phrasing, and a small evidence-update rule (independent agreement
reinforces via noisy-OR; disagreement discounts). No Home Assistant import,
deterministic, never raises. Nothing live produces :class:`Uncertain` values yet
— perception / world-model / prediction wrap their outputs in shadow, parity
against current confidences, enforce when a decision gates on the band. It pairs
naturally with ``beliefs`` and ``provenance``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

# Epistemic bands by confidence. Deliberately conservative: only near-certainty
# counts as "known", and below a third we admit we're essentially guessing.
KNOWN = "known"
BELIEVED = "believed"
GUESSED = "guessed"
UNKNOWN = "unknown"

KNOWN_THRESHOLD = 0.95
BELIEVED_THRESHOLD = 0.60
GUESSED_THRESHOLD = 0.30

# Default bar a consumer should clear before *acting* on an uncertain value.
DEFAULT_ACTIONABLE = BELIEVED_THRESHOLD


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


def band_for(confidence: float) -> str:
    """Classify a confidence into an epistemic band. Pure and total."""
    c = _clamp01(confidence)
    if c >= KNOWN_THRESHOLD:
        return KNOWN
    if c >= BELIEVED_THRESHOLD:
        return BELIEVED
    if c >= GUESSED_THRESHOLD:
        return GUESSED
    return UNKNOWN


@dataclass(frozen=True)
class Uncertain:
    """A value held with a confidence, a band, its basis, and what resolves it.

    ``value`` is the proposition's current best answer; ``confidence`` is clamped
    to ``[0, 1]``; ``basis`` is what the belief rests on (free text / source);
    ``resolver`` is the evidence that *would* settle it (e.g. "open the garage
    camera"). A low-confidence value is not a lie — it is an admitted guess.
    """

    value: Any = None
    confidence: float = 0.0
    basis: str = ""
    resolver: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", _clamp01(self.confidence))

    @property
    def band(self) -> str:
        return band_for(self.confidence)

    def is_known(self, *, threshold: float = KNOWN_THRESHOLD) -> bool:
        return self.confidence >= _clamp01(threshold)

    def is_actionable(self, *, threshold: float = DEFAULT_ACTIONABLE) -> bool:
        """Whether a consumer should act on this value (vs. seek confirmation)."""
        return self.confidence >= _clamp01(threshold)

    def describe(self) -> str:
        """Honest one-line phrasing matching the band."""
        b = self.band
        if b == KNOWN:
            return f"{self.value}"
        if b == BELIEVED:
            return f"I believe {self.value!r} ({self.confidence:.2f})"
        if b == GUESSED:
            return f"I'm guessing {self.value!r} ({self.confidence:.2f})"
        r = f"; would need {self.resolver}" if self.resolver else ""
        return f"unknown{r}"

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "confidence": self.confidence,
            "band": self.band,
            "basis": self.basis,
            "resolver": self.resolver,
        }

    @classmethod
    def from_dict(cls, d: Mapping) -> "Uncertain":
        return cls(
            value=d.get("value"),
            confidence=_clamp01(d.get("confidence", 0.0)),
            basis=str(d.get("basis", "") or ""),
            resolver=str(d.get("resolver", "") or ""),
        )


def assess(
    value: Any,
    confidence: float,
    *,
    basis: str = "",
    resolver: str = "",
) -> Uncertain:
    """Build an :class:`Uncertain`. Pure: the caller supplies the estimate."""
    return Uncertain(value=value, confidence=_clamp01(confidence),
                     basis=str(basis or ""), resolver=str(resolver or ""))


def known(value: Any, *, basis: str = "") -> Uncertain:
    """A value we are certain of (confidence 1.0)."""
    return Uncertain(value=value, confidence=1.0, basis=basis)


def unknown(*, resolver: str = "", basis: str = "") -> Uncertain:
    """An admitted unknown (confidence 0.0), ideally naming what would resolve it."""
    return Uncertain(value=None, confidence=0.0, basis=basis, resolver=resolver)


def update(prior: Uncertain, *, agrees: bool, confidence: float,
           basis: str = "") -> Uncertain:
    """Fold a new piece of evidence into a prior belief about the same value.

    Agreeing evidence reinforces via noisy-OR (``1-(1-cp)(1-ce)``); disagreeing
    evidence discounts the prior by the new evidence's strength
    (``cp*(1-ce)``). The value and resolver carry over; ``basis`` appends.
    Pure and total.
    """
    ce = _clamp01(confidence)
    if agrees:
        new_conf = _clamp01(1.0 - (1.0 - prior.confidence) * (1.0 - ce))
    else:
        new_conf = _clamp01(prior.confidence * (1.0 - ce))
    merged_basis = basis if not prior.basis else (
        f"{prior.basis}; {basis}" if basis else prior.basis)
    return Uncertain(value=prior.value, confidence=new_conf,
                     basis=merged_basis, resolver=prior.resolver)


def most_certain(items: Iterable[Uncertain]) -> Optional[Uncertain]:
    """The highest-confidence value in a set, or None if empty. Pure and total."""
    best: Optional[Uncertain] = None
    for u in items:
        if not isinstance(u, Uncertain):
            continue
        if best is None or u.confidence > best.confidence:
            best = u
    return best
