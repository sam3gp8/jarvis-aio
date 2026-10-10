"""Phase W prerequisite (pure) — information-flow / privacy boundary.

`kernel/privacy.py` decides whether a labelled data item may flow to an audience.
The load-bearing, fail-closed rule: a PERSONAL/SENSITIVE item about person A never
flows to a different person B without A's explicit consent (SENSITIVE also needs a
matching purpose); an unknown classification denies.
"""
import pytest


@pytest.fixture
def P(load):
    return load("kernel.privacy")


def _item(P, cls, subject="", purpose="", audiences=(), source=""):
    return P.DataItem(classification=cls, subject=subject, purpose=purpose,
                      consented_audiences=frozenset(audiences), source=source)


def test_unknown_classification_is_fail_closed(P):
    it = P.DataItem(classification="mystery", subject="alice")
    assert it.classification == P.SENSITIVE             # coerced to most restricted
    assert P.can_disclose(it, audience="bob") is False


def test_public_and_household_flow_freely(P):
    assert P.can_disclose(_item(P, P.PUBLIC), audience="anyone") is True
    assert P.can_disclose(_item(P, P.HOUSEHOLD, subject="alice"), audience="bob") is True


def test_subject_always_sees_own(P):
    it = _item(P, P.SENSITIVE, subject="alice", purpose="health")
    assert P.can_disclose(it, audience="alice") is True


def test_personal_blocks_other_person_without_consent(P):
    it = _item(P, P.PERSONAL, subject="alice")
    assert P.can_disclose(it, audience="bob") is False          # the core invariant
    assert P.can_disclose(it, audience="dad", owner="dad") is True   # owner may see personal
    # consented audience sees it
    it2 = _item(P, P.PERSONAL, subject="alice", audiences=("bob",))
    assert P.can_disclose(it2, audience="bob") is True


def test_sensitive_needs_consent_and_matching_purpose(P):
    it = _item(P, P.SENSITIVE, subject="alice", purpose="health", audiences=("nurse",))
    assert P.can_disclose(it, audience="nurse", purpose="health") is True
    assert P.can_disclose(it, audience="nurse", purpose="marketing") is False   # purpose mismatch
    assert P.can_disclose(it, audience="bob", purpose="health") is False        # not consented
    # owner does NOT automatically get sensitive without consent
    assert P.can_disclose(it, audience="dad", purpose="health", owner="dad") is False


def test_cross_subject_leak_flags_improper_flow(P):
    it = _item(P, P.PERSONAL, subject="alice")
    assert P.cross_subject_leak(it, audience="bob") is True        # A→B, no consent → leak
    assert P.cross_subject_leak(it, audience="alice") is False     # to self → not a leak
    it_ok = _item(P, P.PERSONAL, subject="alice", audiences=("bob",))
    assert P.cross_subject_leak(it_ok, audience="bob") is False    # consented → not a leak
    assert P.cross_subject_leak(_item(P, P.HOUSEHOLD, subject="alice"), audience="bob") is False


def test_redact_withholds_undisclosable(P):
    items = [_item(P, P.PUBLIC),
             _item(P, P.PERSONAL, subject="alice"),
             _item(P, P.PERSONAL, subject="alice", audiences=("bob",))]
    kept = P.redact(items, audience="bob")
    # public + the bob-consented personal item survive; alice's private one is withheld.
    assert len(kept) == 2


def test_defensive_on_bad_input(P):
    assert P.can_disclose(None, audience="x") is False
    assert P.can_disclose(_item(P, P.PERSONAL, subject="a"), audience="") is False
    assert P.redact(None, audience="x") == []
