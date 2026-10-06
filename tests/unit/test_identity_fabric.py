"""Tests for the Identity & Trust Fabric (roadmap Phase I½ — pure).

No Home Assistant, no DB: plain assertions and a deterministic clock. The core
invariant under test is identity ≠ presence ≠ authority ≠ trust, and that an
ambiguous identity fails toward confirmation rather than silently picking one.
"""
import pytest


@pytest.fixture
def idf(load):
    return load("kernel.identity_fabric")


def test_confidence_is_clamped_into_unit_interval(idf):
    assert idf.IdentityAssertion(subject="sam", confidence=2.5).confidence == 1.0
    assert idf.IdentityAssertion(subject="sam", confidence=-3).confidence == 0.0
    assert idf.IdentityAssertion(subject="sam", confidence="nope").confidence == 0.0


def test_presence_never_establishes_identity(idf):
    # A presence signal says *someone* is here, not *who* — the identity ≠
    # presence rule. Even at full confidence it cannot establish identity.
    p = idf.presence_signal(confidence=1.0, now=lambda: 100.0)
    assert p.authentication_method == idf.METHOD_PRESENCE
    assert p.is_identifying() is False
    assert p.establishes_identity(now=100.0) is False
    assert p.subject == ""


def test_identifying_method_establishes_only_when_confident(idf):
    a = idf.assert_identity("sam", method=idf.METHOD_FACE, confidence=0.9, now=lambda: 10.0)
    assert a.is_identifying() is True
    assert a.establishes_identity(now=10.0) is True
    weak = idf.assert_identity("sam", method=idf.METHOD_FACE, confidence=0.4, now=lambda: 10.0)
    assert weak.establishes_identity(now=10.0) is False  # below default min
    assert weak.establishes_identity(now=10.0, min_confidence=0.3) is True


def test_expiry_decays_an_assertion(idf):
    a = idf.assert_identity("sam", method=idf.METHOD_TOKEN, confidence=0.95,
                            now=lambda: 100.0, ttl=60.0)
    assert a.expires_ts == 160.0
    assert a.is_expired(now=159.0) is False
    assert a.is_expired(now=160.0) is True
    # An expired assertion, however confident, no longer establishes identity.
    assert a.establishes_identity(now=200.0) is False
    # No ttl → no expiry.
    forever = idf.assert_identity("sam", method=idf.METHOD_TOKEN, confidence=0.95)
    assert forever.expires_ts is None and forever.is_expired(now=1e12) is False


def test_corroborate_reinforces_same_subject_via_noisy_or(idf):
    a = idf.assert_identity("sam", method=idf.METHOD_FACE, confidence=0.6)
    b = idf.assert_identity("sam", method=idf.METHOD_VOICEPRINT, confidence=0.6)
    # 1 - (1-.6)(1-.6) = 0.84 — independent evidence reinforces.
    assert idf.corroborate(a, b) == pytest.approx(0.84)
    # Different subjects have nothing to corroborate.
    c = idf.assert_identity("lee", method=idf.METHOD_FACE, confidence=0.9)
    assert idf.corroborate(a, c) == 0.0


def test_resolve_picks_corroborated_subject(idf):
    out = idf.resolve(
        [
            idf.assert_identity("sam", method=idf.METHOD_FACE, confidence=0.6, now=lambda: 1.0),
            idf.assert_identity("sam", method=idf.METHOD_VOICEPRINT, confidence=0.6, now=lambda: 1.0),
        ],
        now=5.0,
    )
    assert out.subject == "sam"
    assert out.confidence == pytest.approx(0.84)
    assert out.established is True
    assert out.contested is False
    assert len(out.supporting) == 2


def test_resolve_contested_identities_fail_toward_confirmation(idf):
    # Two different subjects with near-equal confidence → contested, NOT
    # established: an ambiguous identity must defer to a confirmation step.
    out = idf.resolve(
        [
            idf.assert_identity("sam", method=idf.METHOD_FACE, confidence=0.82, now=lambda: 1.0),
            idf.assert_identity("lee", method=idf.METHOD_FACE, confidence=0.80, now=lambda: 1.0),
        ],
        now=2.0,
    )
    assert out.contested is True
    assert out.established is False
    assert out.subject == "sam"       # still reports the top candidate…
    assert out.runner_up == "lee"     # …and the one it couldn't rule out


def test_resolve_ignores_presence_and_expired(idf):
    out = idf.resolve(
        [
            idf.presence_signal(confidence=1.0, now=lambda: 1.0),                  # not identifying
            idf.assert_identity("sam", method=idf.METHOD_FACE, confidence=0.9,
                                now=lambda: 1.0, ttl=10.0),                        # expires at 11
        ],
        now=50.0,  # past the face assertion's expiry
    )
    assert out.subject is None and out.established is False


def test_resolve_empty_is_total(idf):
    out = idf.resolve([], now=1.0)
    assert out.subject is None and out.established is False and out.contested is False
    assert out.supporting == ()


def test_assertion_round_trips_through_dict(idf):
    a = idf.assert_identity(
        "sam", source=idf.SOURCE_CAMERA, method=idf.METHOD_FACE, confidence=0.77,
        now=lambda: 42.0, ttl=30.0, evidence=["frigate:0.77"], scope=["greeting"],
    )
    b = idf.IdentityAssertion.from_dict(a.to_dict())
    assert b == a
