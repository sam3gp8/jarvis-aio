"""Information-flow / privacy boundary (roadmap Phase W prerequisite) — pure.

Phase W (Social & Relationship Intelligence) personalizes per person, and the
audit is explicit that consent-gated + purgeable is **not enough**: W needs an
explicit **information-flow policy** that lands *before* it. This primitive is
that policy — a pure decision over a labelled data item: its classification,
subject, purpose, consent, retention, audience, source and provenance. The
load-bearing rule, encoded structurally, is **non-disclosure across subjects**:
a PERSONAL/SENSITIVE item about person A is never disclosable to a different
person B without A's explicit consent for that purpose, and SENSITIVE always
requires consent + a matching purpose.

Pure: no Home Assistant import, no I/O, no clock, no storage — items and the
requested flow are injected, and :func:`can_disclose` / :func:`redact` are total,
deterministic, **fail-closed** derivations (an unknown classification denies). A
live binder (the social model + any cross-person surfacing) consults it at the
shadow rung; nothing live consumes it yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

# Classification, least → most restricted.
PUBLIC = "public"         # anyone
HOUSEHOLD = "household"   # any resident
PERSONAL = "personal"     # the subject (+ owner); another person needs consent
SENSITIVE = "sensitive"   # the subject only, with explicit consent + purpose
CLASSES = (PUBLIC, HOUSEHOLD, PERSONAL, SENSITIVE)
_RANK = {PUBLIC: 0, HOUSEHOLD: 1, PERSONAL: 2, SENSITIVE: 3}


def _norm(value) -> str:
    return str(value or "").strip().lower()


def classification_rank(classification: str) -> int:
    """Restriction rank; an UNKNOWN classification ranks maximally restricted
    (fail-closed). Total."""
    return _RANK.get(_norm(classification), _RANK[SENSITIVE])


@dataclass(frozen=True)
class DataItem:
    """A labelled piece of information subject to the flow policy. ``subject`` is
    the person it is about (``""`` = not person-specific); ``consented_audiences``
    are the normalized identities the subject has explicitly allowed; ``purpose``
    is what it may be used for. Values coerced so a malformed item is still total
    — and an unknown/blank classification is treated as SENSITIVE (fail-closed)."""

    classification: str = SENSITIVE
    subject: str = ""
    purpose: str = ""
    consented_audiences: frozenset = frozenset()
    source: str = ""

    def __post_init__(self):
        c = _norm(self.classification)
        object.__setattr__(self, "classification", c if c in CLASSES else SENSITIVE)
        object.__setattr__(self, "subject", _norm(self.subject))
        object.__setattr__(self, "purpose", _norm(self.purpose))
        object.__setattr__(self, "source", _norm(self.source))
        aud = self.consented_audiences or ()
        object.__setattr__(self, "consented_audiences",
                           frozenset(_norm(a) for a in aud if _norm(a)))

    def to_dict(self) -> dict:
        return {"classification": self.classification, "subject": self.subject,
                "purpose": self.purpose,
                "consented_audiences": sorted(self.consented_audiences),
                "source": self.source}


def can_disclose(item: DataItem, *, audience: str, purpose: str = "",
                 owner: str = "") -> bool:
    """May ``item`` be disclosed to ``audience`` for ``purpose``? **Fail-closed** —
    an invalid item or anything not explicitly permitted returns False. Rules:

    - PUBLIC → always;
    - HOUSEHOLD → any audience (residents) — kept simple; the live layer supplies
      the resident set;
    - the **subject themselves** may always see their own item;
    - the **owner** may see PERSONAL (not SENSITIVE without consent);
    - any **other** audience needs the subject's explicit consent for that
      audience, and SENSITIVE additionally needs a matching ``purpose``.

    The invariant: a PERSONAL/SENSITIVE item about A never flows to a different
    person B merely because B asked. Total; never raises."""
    if not isinstance(item, DataItem):
        return False
    aud = _norm(audience)
    if not aud:
        return False
    cls = item.classification

    if cls == PUBLIC:
        return True
    if cls == HOUSEHOLD:
        return True
    # PERSONAL / SENSITIVE are subject-scoped.
    if item.subject and aud == item.subject:
        return True                      # your own data
    consented = aud in item.consented_audiences
    if cls == PERSONAL:
        if owner and aud == _norm(owner):
            return True                  # the owner may see personal items
        return consented
    # SENSITIVE: explicit consent for THIS audience AND a matching purpose.
    if cls == SENSITIVE:
        return consented and bool(item.purpose) and _norm(purpose) == item.purpose
    return False                         # unknown → denied


def redact(items: Iterable[DataItem], *, audience: str, purpose: str = "",
           owner: str = "") -> List[DataItem]:
    """The subset of ``items`` disclosable to ``audience`` for ``purpose`` — the
    rest are withheld. Total; never raises."""
    return [it for it in (items or [])
            if isinstance(it, DataItem)
            and can_disclose(it, audience=audience, purpose=purpose, owner=owner)]


def cross_subject_leak(item: DataItem, *, audience: str, purpose: str = "",
                       owner: str = "") -> bool:
    """True when disclosing ``item`` to ``audience`` would reveal one person's
    PERSONAL/SENSITIVE data to a *different* person — the exact boundary W must not
    cross. Useful as an assertion/guard at the shadow rung. Total."""
    if not isinstance(item, DataItem) or not item.subject:
        return False
    aud = _norm(audience)
    if aud == item.subject:
        return False
    if classification_rank(item.classification) < _RANK[PERSONAL]:
        return False                     # public/household is not a cross-subject leak
    # A leak iff the policy would NOT authorize this disclosure to B: an authorized
    # (consented) flow to B is not a leak; an unauthorized one would be.
    return not can_disclose(item, audience=aud, purpose=purpose, owner=owner)
