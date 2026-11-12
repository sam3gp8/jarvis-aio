"""MCU Tranche 1 (foundation trust) — identity → authz PARITY.

The live authority gate builds its AuthorityRequest with identity=None (callers
don't resolve who is asking), so the policy's identity/confidence rules are inert
on the live path: a SECURITY capability is held only by the legacy gate, and a
sensitive action never confirms merely because WHO is present is a shaky guess.
This parity resolves the live principal and logs what the decision WOULD be with
that identity + confidence — a divergence from the identity-blind baseline is a
real finding the enforce rung would act on. Observe-only throughout.

These drive the real entry point (``record_control_parity`` with no identity, the
common live case) rather than hand-building kernel objects, so nothing here loads
the ``jc.kernel`` subpackage in a way that would perturb sibling authority tests.
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


def _parity_warnings(caplog):
    return [r for r in caplog.records
            if r.levelno >= logging.WARNING and "identity->authz(parity)" in r.message]


def _parity_lines(caplog):
    return [r for r in caplog.records if "identity->authz(parity)" in r.message]


# ── divergences the enforce rung would act on ────────────────────────────────

def test_security_cap_diverges_with_identified_requester(ab, idmod, monkeypatch, caplog):
    # SECURITY cap, gate holds (allowed=False): identity-blind baseline is DENY
    # ("no identified requester"); with a resolved requester it would be CONFIRM.
    monkeypatch.setattr(idmod, "quick_identify", lambda h, area_id=None: _ident(idmod, "alice", 0.9))
    with caplog.at_level(logging.DEBUG):
        d = ab.record_control_parity(FakeHass(), "lock", "unlock", allowed=False)
    assert d is not None and d.decision == "deny"        # recorded decision unchanged
    warns = _parity_warnings(caplog)
    assert warns and "alice" in warns[0].message and "confirm" in warns[0].message


def test_sensitive_low_confidence_diverges(ab, idmod, monkeypatch, caplog):
    # SENSITIVE cap at the default confidence is ALLOW; a low-confidence guess
    # about who is present would make it CONFIRM.
    monkeypatch.setattr(idmod, "quick_identify", lambda h, area_id=None: _ident(idmod, "alice", 0.3))
    with caplog.at_level(logging.DEBUG):
        d = ab.record_control_parity(FakeHass(), "light", "turn_on", allowed=True)
    assert d is not None and d.decision == "allow"
    assert _parity_warnings(caplog)


# ── agreement (no divergence) → debug, not warning ───────────────────────────

def test_sensitive_confident_requester_agrees(ab, idmod, monkeypatch, caplog):
    monkeypatch.setattr(idmod, "quick_identify", lambda h, area_id=None: _ident(idmod, "alice", 0.95))
    with caplog.at_level(logging.DEBUG):
        ab.record_control_parity(FakeHass(), "light", "turn_on", allowed=True)
    assert not _parity_warnings(caplog)
    assert _parity_lines(caplog)             # a debug "agrees" line still emitted


def test_unidentified_requester_no_divergence(ab, idmod, monkeypatch, caplog):
    # No one resolved → who=None → same identity-blind decision → no divergence.
    monkeypatch.setattr(idmod, "quick_identify",
                        lambda h, area_id=None: idmod.Identification(person=idmod.UNKNOWN, confidence=0.0))
    with caplog.at_level(logging.DEBUG):
        ab.record_control_parity(FakeHass(), "lock", "unlock", allowed=False)
    assert not _parity_warnings(caplog)


# ── guards: kill-switch, supplied identity, defensiveness ────────────────────

def test_kill_switch_silences(ab, idmod, monkeypatch, caplog):
    monkeypatch.setattr(ab, "IDENTITY_AUTHZ_PARITY", False)
    monkeypatch.setattr(idmod, "quick_identify", lambda h, area_id=None: _ident(idmod, "alice", 0.9))
    with caplog.at_level(logging.DEBUG):
        ab.record_control_parity(FakeHass(), "lock", "unlock", allowed=False)
    assert not _parity_lines(caplog)


def test_supplied_identity_skips_parity(ab, idmod, monkeypatch, caplog):
    # When the caller already resolved a requester there is no gap to measure.
    monkeypatch.setattr(idmod, "quick_identify", lambda h, area_id=None: _ident(idmod, "alice", 0.9))
    with caplog.at_level(logging.DEBUG):
        ab.record_control_parity(FakeHass(), "lock", "unlock", allowed=False, identity="bob")
    assert not _parity_lines(caplog)


def test_identify_failure_never_disrupts(ab, idmod, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("identity down")
    monkeypatch.setattr(idmod, "quick_identify", _boom)
    # the recorded decision must still come back; the parity consult fails silently
    d = ab.record_control_parity(FakeHass(), "lock", "unlock", allowed=False)
    assert d is not None and d.decision == "deny"


def test_none_baseline_is_noop(ab):
    ab._emit_identity_authz_parity(FakeHass(), "lock.unlock", None)   # never raises
