"""Tests for the uncertainty primitive (Epistemic Fabric — pure)."""
import pytest


@pytest.fixture
def un(load):
    return load("kernel.uncertainty")


def test_bands_by_confidence(un):
    assert un.band_for(0.99) == un.KNOWN
    assert un.band_for(0.8) == un.BELIEVED
    assert un.band_for(0.4) == un.GUESSED
    assert un.band_for(0.1) == un.UNKNOWN
    assert un.band_for(5.0) == un.KNOWN      # clamped
    assert un.band_for(-1) == un.UNKNOWN


def test_confidence_clamped(un):
    assert un.Uncertain(value=1, confidence=2.0).confidence == 1.0
    assert un.Uncertain(value=1, confidence=-1).confidence == 0.0


def test_is_known_and_actionable(un):
    u = un.assess("empty", 0.7)
    assert u.band == un.BELIEVED
    assert u.is_known() is False            # below 0.95
    assert u.is_actionable() is True        # at/above 0.60 default
    assert u.is_actionable(threshold=0.8) is False


def test_describe_matches_band(un):
    assert un.known("locked").describe() == "locked"
    assert "I believe" in un.assess("empty", 0.8).describe()
    assert "guessing" in un.assess("maybe", 0.4).describe()
    d = un.unknown(resolver="open the camera").describe()
    assert d.startswith("unknown") and "open the camera" in d


def test_known_and_unknown_builders(un):
    assert un.known(5).confidence == 1.0
    u = un.unknown(resolver="check sensor")
    assert u.confidence == 0.0 and u.value is None and u.resolver == "check sensor"


def test_update_agreeing_reinforces(un):
    prior = un.assess("empty", 0.6, basis="camera")
    out = un.update(prior, agrees=True, confidence=0.6, basis="motion")
    assert out.value == "empty"
    assert out.confidence == pytest.approx(0.84)   # 1-(1-.6)(1-.6)
    assert "camera" in out.basis and "motion" in out.basis


def test_update_disagreeing_discounts(un):
    prior = un.assess("empty", 0.8)
    out = un.update(prior, agrees=False, confidence=0.5)
    assert out.confidence == pytest.approx(0.4)     # 0.8*(1-0.5)


def test_most_certain_picks_highest(un):
    items = [un.assess("a", 0.3), un.assess("b", 0.9), un.assess("c", 0.6)]
    assert un.most_certain(items).value == "b"
    assert un.most_certain([]) is None


def test_round_trips_through_dict(un):
    u = un.assess({"x": 1}, 0.77, basis="vision", resolver="open door")
    back = un.Uncertain.from_dict(u.to_dict())
    assert back == u
