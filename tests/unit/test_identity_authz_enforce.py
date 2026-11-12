"""MCU Tranche 1 (foundation trust) — identity → authz ENFORCE.

The parity rung proved, log-only, that resolving WHO is asking changes the
engine's verdict on real traffic. This rung makes that resolution bite — but only
ever to TIGHTEN. When authority enforce is on and the caller passed no identity
(the common live case), ``enforced_decision`` resolves the live principal and, for
a KNOWN requester, re-runs the policy with that identity + confidence, folding the
verdict into the SAME max-restriction belt. The one effect on an action the
identity-blind gate would ALLOW is to hold a SENSITIVE one for confirmation when
the requester is a low-confidence guess; an unidentified requester or any fault
leaves the identity-blind outcome unchanged (tighten-only / fail-safe).

These drive the real entry point (``enforced_decision`` / ``_identity_aware_decision``)
and only monkeypatch ``identity.quick_identify`` — nothing here loads the
``jc.kernel`` subpackage as a fixture, which would perturb sibling authority tests.
"""
import logging

import pytest

from fakes import FakeHass


@pytest.fixture
def ab(load):
    return load("authority_bridge")


@pytest.fixture
def idmod(load):
    return load("identity")


def _ident(idmod, person="alice", confidence=0.9):
    return idmod.Identification(person=person, confidence=confidence, method="face")


def _see(idmod, monkeypatch, person="alice", confidence=0.9):
    """Make quick_identify resolve a given principal."""
    monkeypatch.setattr(idmod, "quick_identify",
                        lambda h, area_id=None: _ident(idmod, person, confidence))


def _unidentified(idmod, monkeypatch):
    monkeypatch.setattr(
        idmod, "quick_identify",
        lambda h, area_id=None: idmod.Identification(person=idmod.UNKNOWN, confidence=0.0))


def _enforce_warnings(caplog):
    return [r for r in caplog.records
            if r.levelno >= logging.WARNING and "identity->authz ENFORCE" in r.message]


def _authority_enforce_warnings(caplog):
    # the identity-blind belt's own hold message (distinct string)
    return [r for r in caplog.records
            if r.levelno >= logging.WARNING and r.message.startswith("authority ENFORCE")]


# ── the tighten: known-but-low-confidence requester holds a sensitive ALLOW ──

def test_sensitive_low_confidence_requester_is_held(ab, idmod, monkeypatch, caplog):
    _see(idmod, monkeypatch, "alice", 0.3)
    with caplog.at_level(logging.DEBUG):
        ok, d = ab.enforced_decision(FakeHass(), "light", "turn_on", legacy_ok=True)
    assert ok is False                       # tightened: ALLOW → held
    assert _enforce_warnings(caplog)
    # the recorded (identity-blind) decision is unchanged — still ALLOW
    assert d is not None and d.decision == "allow"


def test_sensitive_confident_requester_still_allowed(ab, idmod, monkeypatch, caplog):
    _see(idmod, monkeypatch, "alice", 0.95)
    with caplog.at_level(logging.DEBUG):
        ok, d = ab.enforced_decision(FakeHass(), "light", "turn_on", legacy_ok=True)
    assert ok is True                        # high confidence → no tightening
    assert not _enforce_warnings(caplog)


# ── unidentified requester never changes the identity-blind outcome ──────────

def test_unidentified_requester_no_change(ab, idmod, monkeypatch, caplog):
    _unidentified(idmod, monkeypatch)
    with caplog.at_level(logging.DEBUG):
        ok, _ = ab.enforced_decision(FakeHass(), "light", "turn_on", legacy_ok=True)
    assert ok is True                        # identity stays None → baseline stands
    assert not _enforce_warnings(caplog)


# ── tighten-only: a held action is never loosened ────────────────────────────

def test_cannot_loosen_a_held_gate(ab, idmod, monkeypatch):
    # The legacy gate already holds the action; the sharpest possible identity
    # (high confidence) can never turn that into an allow.
    _see(idmod, monkeypatch, "alice", 0.99)
    ok, _ = ab.enforced_decision(FakeHass(), "light", "turn_on", legacy_ok=False)
    assert ok is False


def test_security_cap_already_held_by_belt_not_identity(ab, idmod, monkeypatch, caplog):
    # SECURITY cap with no supplied identity is DENY'd identity-blind, so the belt
    # already holds it; the identity rung must not even run (its guard is effective_ok).
    _see(idmod, monkeypatch, "alice", 0.9)
    with caplog.at_level(logging.DEBUG):
        ok, d = ab.enforced_decision(FakeHass(), "lock", "unlock", legacy_ok=True)
    assert ok is False
    assert not _enforce_warnings(caplog)         # not the identity rung's doing
    assert _authority_enforce_warnings(caplog)   # the identity-blind belt held it


# ── SAFE capabilities are never tightened, even at zero confidence ───────────

def test_safe_cap_never_tightened(ab, idmod, monkeypatch, caplog):
    _see(idmod, monkeypatch, "alice", 0.01)
    with caplog.at_level(logging.DEBUG):
        ok, _ = ab.enforced_decision(FakeHass(), "media_player", "play_media", legacy_ok=True)
    assert ok is True
    assert not _enforce_warnings(caplog)


# ── a supplied identity short-circuits the live resolution entirely ──────────

def test_supplied_identity_skips_live_resolution(ab, idmod, monkeypatch, caplog):
    # Caller already resolved bob (confident) → baseline ALLOW. A low-confidence
    # live reading of alice must be ignored: the rung only fills a gap that isn't
    # there, so quick_identify is never consulted and nothing is tightened.
    calls = {"n": 0}

    def _spy(h, area_id=None):
        calls["n"] += 1
        return _ident(idmod, "alice", 0.2)
    monkeypatch.setattr(idmod, "quick_identify", _spy)
    with caplog.at_level(logging.DEBUG):
        ok, _ = ab.enforced_decision(
            FakeHass(), "light", "turn_on", legacy_ok=True,
            identity="bob", confidence=0.95)
    assert ok is True
    assert calls["n"] == 0                       # no live resolution attempted
    assert not _enforce_warnings(caplog)


# ── kill-switch & fail-safe ──────────────────────────────────────────────────

def test_kill_switch_reverts_to_parity_only(ab, idmod, monkeypatch, caplog):
    monkeypatch.setattr(ab, "IDENTITY_AUTHZ_ENFORCE", False)
    _see(idmod, monkeypatch, "alice", 0.3)       # would tighten if the rung were on
    with caplog.at_level(logging.DEBUG):
        ok, _ = ab.enforced_decision(FakeHass(), "light", "turn_on", legacy_ok=True)
    assert ok is True                            # enforce off → baseline ALLOW stands
    assert not _enforce_warnings(caplog)


def test_identify_fault_is_fail_safe(ab, idmod, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("identity down")
    monkeypatch.setattr(idmod, "quick_identify", _boom)
    # a fault in resolution can never block a legitimate action
    ok, d = ab.enforced_decision(FakeHass(), "light", "turn_on", legacy_ok=True)
    assert ok is True
    assert d is not None and d.decision == "allow"


# ── _identity_aware_decision unit behaviour ──────────────────────────────────

def test_aware_decision_confirms_on_low_confidence(ab, idmod, monkeypatch):
    _see(idmod, monkeypatch, "alice", 0.3)
    d = ab._identity_aware_decision(FakeHass(), "light.turn_on")
    assert d is not None and d.decision == "confirm"


def test_aware_decision_allows_on_high_confidence(ab, idmod, monkeypatch):
    _see(idmod, monkeypatch, "alice", 0.95)
    d = ab._identity_aware_decision(FakeHass(), "light.turn_on")
    assert d is not None and d.decision == "allow"


def test_aware_decision_none_when_unidentified(ab, idmod, monkeypatch):
    _unidentified(idmod, monkeypatch)
    assert ab._identity_aware_decision(FakeHass(), "light.turn_on") is None


def test_aware_decision_none_when_kill_switch_off(ab, idmod, monkeypatch):
    monkeypatch.setattr(ab, "IDENTITY_AUTHZ_ENFORCE", False)
    _see(idmod, monkeypatch, "alice", 0.3)
    assert ab._identity_aware_decision(FakeHass(), "light.turn_on") is None


def test_aware_decision_none_on_fault(ab, idmod, monkeypatch):
    monkeypatch.setattr(idmod, "quick_identify",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ab._identity_aware_decision(FakeHass(), "light.turn_on") is None
