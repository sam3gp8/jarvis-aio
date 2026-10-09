"""Uncertainty shadow on the identity fusion read (#237, Epistemic Fabric).

``identity.resolve()`` fuses presence / face / room / voice signals into a
best-guess person + a computed confidence. In shadow it also packages that
verdict as a ``kernel.uncertainty.Uncertain`` (value=person, the fused
confidence, basis=the methods that voted, a resolver for a weak read) and logs
its epistemic band — the "perception wraps its output" rung. The emission is
observe-only: the returned ``Identification`` is unchanged whether the shadow
runs or not, and the log-only path never raises.
"""
import logging

import pytest


@pytest.fixture
def identity(load):
    return load("identity")


@pytest.fixture
def cfg(load, monkeypatch):
    jc = load("jarvis_config")
    store = {}
    monkeypatch.setattr(jc, "get", lambda k, d=None: store.get(k, d))
    return store


@pytest.fixture
def sigs(load, monkeypatch):
    presence = load("presence")
    recognition = load("recognition")
    state = {"home": [], "seen": {}, "last": {}}
    monkeypatch.setattr(
        presence, "get_presence_summary",
        lambda hass: {"people": [{"name": n, "state": "home"} for n in state["home"]]})
    monkeypatch.setattr(recognition, "who_is_where", lambda hass: dict(state["seen"]))
    monkeypatch.setattr(recognition, "last_seen_at",
                        lambda hass, cam: state["last"].get(cam))
    return state


def test_resolve_identical_with_uncertainty_shadow_on_or_off(identity, cfg, sigs, fake_hass):
    """The core shadow guarantee: the identity verdict is unchanged whether the
    uncertainty emission runs or not."""
    sigs["home"] = ["Sam"]
    identity.UNCERTAINTY_SHADOW = True
    with_shadow = identity.resolve(fake_hass)
    identity.UNCERTAINTY_SHADOW = False
    try:
        without_shadow = identity.resolve(fake_hass)
    finally:
        identity.UNCERTAINTY_SHADOW = True
    assert with_shadow.person == without_shadow.person == "Sam"
    assert with_shadow.confidence == without_shadow.confidence
    assert with_shadow.method == without_shadow.method


def test_resolve_logs_uncertainty_band(identity, cfg, sigs, fake_hass, caplog):
    """A confident sole-occupant read logs an honest band, not a bare name."""
    sigs["home"] = ["Sam"]
    with caplog.at_level(logging.DEBUG):
        ident = identity.resolve(fake_hass)
    assert ident.person == "Sam"
    msgs = [r.message for r in caplog.records
            if "identity_uncertainty(shadow)" in r.message]
    assert msgs, "expected an identity_uncertainty(shadow) log line"
    # A lone sole-occupant vote resolves to confidence 0.60 → the 'believed' band.
    assert "band=believed" in msgs[-1]
    assert "sole_occupant" in msgs[-1]


def test_no_signal_emits_no_uncertainty(identity, cfg, sigs, fake_hass, caplog):
    """resolve() returns before the fusion point when there are no votes, so no
    uncertainty view is produced for an empty read."""
    with caplog.at_level(logging.DEBUG):
        ident = identity.resolve(fake_hass)
    assert ident.person == "unknown" and ident.method == "no_signal"
    assert not [r for r in caplog.records
                if "identity_uncertainty(shadow)" in r.message]


def test_uncertainty_shadow_helper_is_defensive(identity):
    """The log-only path never raises on odd input."""
    identity._emit_identity_uncertainty_shadow("Sam", 0.6, {"face"}, True)
    identity._emit_identity_uncertainty_shadow("Sam", 0.6, None, False)
    identity._emit_identity_uncertainty_shadow("", 0.0, set(), False)
