"""Social & relationship intelligence (roadmap Phase W) — pure model primitive.

Per-person preference models that personalize **within strict consent / privacy
limits**. Built directly ON the information-flow boundary (:mod:`kernel.privacy`):
a `Preference` is a labelled datum with a subject and consent, and reading one for
a *different* person routes through `privacy.can_disclose` — so Person A's
preference is never disclosed to Person B without A's explicit consent. The model
is consent-gated, owner-inspectable and **purgeable**, and it **never drives a
security/intrusion decision** — structurally it holds no actuator and exposes only
preference values.

Pure: no Home Assistant import, no I/O, no clock, no storage — the model is built
from injected preferences and every derivation is total and deterministic. A live
binder (a preference learner + the personalization call-sites) wires it on at the
shadow rung; nothing live consumes it yet.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Optional

from . import privacy


def _norm(value) -> str:
    return str(value or "").strip().lower()


def _clamp01(x) -> float:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f < 0.0 else 1.0 if f > 1.0 else f


@dataclass(frozen=True)
class Preference:
    """One learned preference belonging to ``subject``. ``classification`` +
    ``consented_audiences`` are the privacy labels that gate cross-person reads
    (default PERSONAL — the subject and owner, nobody else without consent)."""

    subject: str
    key: str
    value: str
    confidence: float = 0.5
    classification: str = privacy.PERSONAL
    consented_audiences: frozenset = frozenset()

    def __post_init__(self):
        object.__setattr__(self, "subject", _norm(self.subject))
        object.__setattr__(self, "key", _norm(self.key))
        object.__setattr__(self, "value", str(self.value if self.value is not None else "").strip())
        object.__setattr__(self, "confidence", _clamp01(self.confidence))
        c = _norm(self.classification)
        object.__setattr__(self, "classification",
                           c if c in privacy.CLASSES else privacy.PERSONAL)
        aud = self.consented_audiences or ()
        object.__setattr__(self, "consented_audiences",
                           frozenset(_norm(a) for a in aud if _norm(a)))

    def as_data_item(self, *, purpose: str = "") -> "privacy.DataItem":
        """Project onto a privacy.DataItem so the boundary policy can rule on it."""
        return privacy.DataItem(
            classification=self.classification, subject=self.subject,
            purpose=purpose, consented_audiences=self.consented_audiences,
            source="social")

    def to_dict(self) -> dict:
        return {"subject": self.subject, "key": self.key, "value": self.value,
                "confidence": self.confidence, "classification": self.classification,
                "consented_audiences": sorted(self.consented_audiences)}


@dataclass(frozen=True)
class PersonModel:
    """A person's preference set, keyed by preference key (newest wins). Immutable
    — :func:`remember` / :func:`purge` return a new model."""

    subject: str
    preferences: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "subject", _norm(self.subject))
        prefs = tuple(p for p in (self.preferences or ())
                      if isinstance(p, Preference) and p.key and p.subject == _norm(self.subject))
        # de-dup by key, last occurrence wins
        seen: Dict[str, Preference] = {}
        for p in prefs:
            seen[p.key] = p
        object.__setattr__(self, "preferences", tuple(seen.values()))

    def get(self, key: str) -> Optional[Preference]:
        k = _norm(key)
        for p in self.preferences:
            if p.key == k:
                return p
        return None

    @property
    def is_empty(self) -> bool:
        return not self.preferences

    def to_dict(self) -> dict:
        return {"subject": self.subject,
                "preferences": [p.to_dict() for p in self.preferences]}


def model_for(subject: str, preferences=None) -> PersonModel:
    """Build a person model. Pure constructor."""
    return PersonModel(subject=subject, preferences=tuple(preferences or ()))


def remember(model: PersonModel, pref: Preference) -> PersonModel:
    """Add/replace a preference (same key → newest wins). A preference whose
    subject differs from the model's is ignored. Total; never raises."""
    if not isinstance(model, PersonModel) or not isinstance(pref, Preference):
        return model if isinstance(model, PersonModel) else model_for("")
    if pref.subject != model.subject or not pref.key:
        return model
    kept = tuple(p for p in model.preferences if p.key != pref.key)
    return replace(model, preferences=kept + (pref,))


def preference(model: PersonModel, key: str) -> Optional[str]:
    """The subject's OWN preference value for ``key`` (self-read, always allowed),
    or None. Total."""
    if not isinstance(model, PersonModel):
        return None
    p = model.get(key)
    return p.value if p else None


def personalize_for(model: PersonModel, key: str, *, audience: str,
                    default: str = "", purpose: str = "", owner: str = "") -> str:
    """The preference value to use **for ``audience``**, respecting the privacy
    boundary: the subject's preference is returned only when
    `privacy.can_disclose` permits it to flow to ``audience``; otherwise the
    non-personalized ``default`` is returned. So personalizing *for* someone never
    reveals another person's private preference. Total; never raises."""
    if not isinstance(model, PersonModel):
        return default
    p = model.get(key)
    if not p:
        return default
    if privacy.can_disclose(p.as_data_item(purpose=purpose),
                            audience=audience, purpose=purpose, owner=owner):
        return p.value
    return default


def purge(model: PersonModel) -> PersonModel:
    """Owner purge — drop every preference for the person. Returns an empty model
    for the same subject. Total."""
    subj = model.subject if isinstance(model, PersonModel) else ""
    return model_for(subj)


def summarize(model: PersonModel) -> dict:
    """Compact roll-up for owner inspection. Never raises."""
    if not isinstance(model, PersonModel):
        return {"subject": "", "preferences": 0, "keys": []}
    return {"subject": model.subject,
            "preferences": len(model.preferences),
            "keys": sorted(p.key for p in model.preferences)}
