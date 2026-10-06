"""Identity & Trust Fabric (roadmap Phase I½ — audit-added).

Authority already carries an *actor*, a *token*, a *scope* and a *confidence*,
but the 2026-10 audit called out that identity deserves a dedicated primitive
before autonomy grows: as JARVIS acts more on its own it must keep straight
*who is speaking, who is present, who owns a request, who delegated it, who
authorized it, how identity was established, how confident we are, and how long
that holds.* This module is that primitive.

The one hard rule it encodes is **identity ≠ presence ≠ authority ≠ trust**:

* An :class:`IdentityAssertion` carries *who*, *how it was established*, a
  *confidence*, an *expiry* and the *evidence* behind it — never a bare boolean.
* A *presence* signal (``METHOD_PRESENCE``) says only that *someone* is there; it
  never establishes *who*, so it cannot, on its own, identify a person.
* Establishing identity is not authorization (that stays with ``kernel.authority``)
  and not earned trust (that is Phase N autonomy).

Phase I½ lands this **pure**: builders, a confidence algebra (expiry, corroboration
via noisy-OR) and a safe resolver that *fails toward confirmation* when two
subjects are too close to call. Nothing live consumes it yet (recognizers emit
assertions in shadow, parity against the current identity reads, enforce on one
identity-sensitive path — owner-gated). No Home Assistant import, so it is
deterministic and unit-testable, and it never raises into a caller.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Mapping, Optional, Tuple

# ── How an identity was established ──────────────────────────────────────────
# Methods that can establish *who* someone is…
METHOD_FACE = "face"
METHOD_VOICEPRINT = "voiceprint"
METHOD_TOKEN = "token"
METHOD_DEVICE = "device"
METHOD_PIN = "pin"
METHOD_MANUAL = "manual"
METHOD_DELEGATED = "delegated"
# …and one that explicitly cannot: presence says *someone* is here, not *who*.
METHOD_PRESENCE = "presence"
METHOD_UNKNOWN = "unknown"

# The methods that may establish identity. Presence and unknown never do — that
# is the identity ≠ presence rule, encoded rather than merely documented.
IDENTIFYING_METHODS = frozenset(
    {
        METHOD_FACE,
        METHOD_VOICEPRINT,
        METHOD_TOKEN,
        METHOD_DEVICE,
        METHOD_PIN,
        METHOD_MANUAL,
        METHOD_DELEGATED,
    }
)

# ── Where an assertion came from ─────────────────────────────────────────────
SOURCE_VOICE = "voice"
SOURCE_CAMERA = "camera"
SOURCE_MOBILE = "mobile"
SOURCE_TOKEN = "token"
SOURCE_DELEGATED = "delegated_agent"
SOURCE_MANUAL = "manual"

# Below this confidence, an assertion is too weak to establish identity on its
# own. A deliberately conservative default: identity-sensitive paths fail toward
# asking for confirmation rather than acting on a shaky guess.
DEFAULT_MIN_CONFIDENCE = 0.6

# When the two strongest candidate subjects are within this margin of each other,
# the identity is "contested" and is NOT established — defer to confirmation.
DEFAULT_CONTEST_MARGIN = 0.15


def _clamp01(x: float) -> float:
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
class IdentityAssertion:
    """A single claim that ``subject`` is present/speaking, with its provenance.

    ``confidence`` is clamped to ``[0, 1]``. ``expires_ts`` is an absolute epoch
    time; ``None`` means "no stated expiry" (still subject to a caller's own
    freshness policy). ``evidence`` and ``scope`` are free-form tuples describing
    what backs the claim and what it is good for.
    """

    subject: str
    source: str = SOURCE_MANUAL
    authentication_method: str = METHOD_UNKNOWN
    confidence: float = 0.0
    asserted_ts: float = 0.0
    expires_ts: Optional[float] = None
    evidence: Tuple[str, ...] = ()
    scope: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Freeze-safe normalization of the confidence into [0, 1].
        object.__setattr__(self, "confidence", _clamp01(self.confidence))

    def is_expired(self, now: Optional[float] = None) -> bool:
        """True once ``expires_ts`` has passed. No expiry never expires."""
        if self.expires_ts is None:
            return False
        clock = now if now is not None else time.time()
        return float(clock) >= float(self.expires_ts)

    def is_identifying(self) -> bool:
        """Whether this assertion's method can establish *who* at all.

        Presence-only and unknown-method assertions return False — they may prove
        that *someone* is there, never *who*.
        """
        return self.authentication_method in IDENTIFYING_METHODS

    def establishes_identity(
        self,
        now: Optional[float] = None,
        *,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ) -> bool:
        """True only if identifying, unexpired, and confident enough.

        This is the single gate a consumer should use before treating an
        assertion as "we know who this is" — it keeps identity distinct from
        both presence (non-identifying method) and a low-confidence guess.
        """
        return (
            self.is_identifying()
            and not self.is_expired(now)
            and self.confidence >= _clamp01(min_confidence)
        )

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "source": self.source,
            "authentication_method": self.authentication_method,
            "confidence": self.confidence,
            "asserted_ts": self.asserted_ts,
            "expires_ts": self.expires_ts,
            "evidence": list(self.evidence),
            "scope": list(self.scope),
        }

    @classmethod
    def from_dict(cls, d: Mapping) -> "IdentityAssertion":
        exp = d.get("expires_ts")
        return cls(
            subject=str(d.get("subject", "")),
            source=str(d.get("source", SOURCE_MANUAL)),
            authentication_method=str(d.get("authentication_method", METHOD_UNKNOWN)),
            confidence=_clamp01(d.get("confidence", 0.0)),
            asserted_ts=float(d.get("asserted_ts", 0.0) or 0.0),
            expires_ts=(float(exp) if exp is not None else None),
            evidence=tuple(str(e) for e in (d.get("evidence") or ())),
            scope=tuple(str(s) for s in (d.get("scope") or ())),
        )


def assert_identity(
    subject: str,
    *,
    source: str = SOURCE_MANUAL,
    method: str = METHOD_UNKNOWN,
    confidence: float = 0.0,
    now: Optional[Callable[[], float]] = None,
    ttl: Optional[float] = None,
    evidence: Iterable[str] = (),
    scope: Iterable[str] = (),
) -> IdentityAssertion:
    """Build an :class:`IdentityAssertion`, stamping ``asserted_ts`` from ``now``.

    ``ttl`` (seconds), when given, sets ``expires_ts = asserted_ts + ttl`` so the
    assertion decays — no assertion is permanent. Pure: the caller supplies the
    extracted recognizer output; this just packages it.
    """
    clock = now or time.time
    ts = float(clock())
    expires = (ts + float(ttl)) if ttl is not None else None
    return IdentityAssertion(
        subject=str(subject),
        source=str(source),
        authentication_method=str(method),
        confidence=_clamp01(confidence),
        asserted_ts=ts,
        expires_ts=expires,
        evidence=tuple(str(e) for e in evidence),
        scope=tuple(str(s) for s in scope),
    )


def presence_signal(
    *,
    source: str = SOURCE_CAMERA,
    confidence: float = 0.0,
    now: Optional[Callable[[], float]] = None,
    ttl: Optional[float] = None,
    evidence: Iterable[str] = (),
) -> IdentityAssertion:
    """A *presence* assertion: someone is here, but not who.

    Returned as an :class:`IdentityAssertion` with ``METHOD_PRESENCE`` and an
    empty subject, so it round-trips through the same plumbing yet can never
    ``establishes_identity`` — the identity ≠ presence rule in code.
    """
    return assert_identity(
        subject="",
        source=source,
        method=METHOD_PRESENCE,
        confidence=confidence,
        now=now,
        ttl=ttl,
        evidence=evidence,
        scope=(),
    )


def corroborate(a: IdentityAssertion, b: IdentityAssertion) -> float:
    """Combined confidence that two assertions about the *same* subject agree.

    Noisy-OR: ``1 - (1 - ca)(1 - cb)`` — independent evidence reinforces, never
    weakens. Returns ``0.0`` if the subjects differ (nothing to corroborate).
    """
    if a.subject != b.subject or not a.subject:
        return 0.0
    return _clamp01(1.0 - (1.0 - a.confidence) * (1.0 - b.confidence))


@dataclass(frozen=True)
class IdentityResolution:
    """The outcome of resolving many assertions into a single identity verdict."""

    subject: Optional[str] = None
    confidence: float = 0.0
    established: bool = False
    contested: bool = False
    supporting: Tuple[IdentityAssertion, ...] = field(default_factory=tuple)
    runner_up: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "confidence": self.confidence,
            "established": self.established,
            "contested": self.contested,
            "runner_up": self.runner_up,
            "supporting": [a.to_dict() for a in self.supporting],
        }


def resolve(
    assertions: Iterable[IdentityAssertion],
    *,
    now: Optional[float] = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    contest_margin: float = DEFAULT_CONTEST_MARGIN,
) -> IdentityResolution:
    """Fold assertions into one identity verdict, failing toward confirmation.

    Only identifying, unexpired assertions are considered (presence/unknown are
    dropped). Assertions for the same subject are corroborated (noisy-OR across
    their confidences). The highest-confidence subject wins — but if a different
    subject is within ``contest_margin`` of it, the result is **contested** and
    ``established`` stays False: an ambiguous identity must defer to a
    confirmation step, never silently pick one. ``established`` is True only when
    a single subject clears ``min_confidence`` uncontested. Pure and total.
    """
    clock = now if now is not None else time.time()
    valid = [
        a
        for a in assertions
        if isinstance(a, IdentityAssertion)
        and a.is_identifying()
        and a.subject
        and not a.is_expired(clock)
    ]
    if not valid:
        return IdentityResolution()

    # Corroborate per subject via noisy-OR over that subject's assertions.
    by_subject: dict[str, float] = {}
    support: dict[str, List[IdentityAssertion]] = {}
    for a in valid:
        prev = by_subject.get(a.subject, 0.0)
        by_subject[a.subject] = _clamp01(1.0 - (1.0 - prev) * (1.0 - a.confidence))
        support.setdefault(a.subject, []).append(a)

    ranked = sorted(by_subject.items(), key=lambda kv: kv[1], reverse=True)
    top_subject, top_conf = ranked[0]
    runner_up, runner_conf = (ranked[1] if len(ranked) > 1 else (None, 0.0))

    contested = runner_up is not None and (top_conf - runner_conf) < _clamp01(contest_margin)
    established = (top_conf >= _clamp01(min_confidence)) and not contested

    return IdentityResolution(
        subject=top_subject,
        confidence=top_conf,
        established=established,
        contested=contested,
        supporting=tuple(support.get(top_subject, ())),
        runner_up=runner_up,
    )
