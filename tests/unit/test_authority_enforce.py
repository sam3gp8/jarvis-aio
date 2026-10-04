"""MCU Phase G (G4): the kernel authority engine is ENFORCED over the live
confirm-gate, via a max-restriction belt.

For an allowlisted security capability the action proceeds only if the legacy
gate allows it AND the kernel engine returns ALLOW — so the engine can only
*tighten* the gate (hold an otherwise-allowed action for confirmation), never
loosen it. Fail-safe to the legacy outcome on any engine fault; kill-switch and
per-capability allowlist bound the blast radius.
"""
from types import SimpleNamespace

import pytest

from fakes import FakeHass


@pytest.fixture
def ab(load):
    return load("authority_bridge")


def _fake(label):
    return SimpleNamespace(decision=label, reason="test")


# ── the max-restriction belt (decision isolated) ────────────────────────────

def test_allowlisted_engine_allow_proceeds(ab, monkeypatch):
    monkeypatch.setattr(ab, "record_control_parity", lambda *a, **k: _fake("allow"))
    ok, _d = ab.enforced_decision(FakeHass(), "lock", "unlock", legacy_ok=True)
    assert ok is True


def test_allowlisted_engine_confirm_is_gated(ab, monkeypatch):
    # The belt: legacy allowed, but the engine would hold → gated.
    monkeypatch.setattr(ab, "record_control_parity", lambda *a, **k: _fake("confirm"))
    ok, _d = ab.enforced_decision(FakeHass(), "lock", "unlock", legacy_ok=True)
    assert ok is False


def test_allowlisted_engine_deny_is_gated(ab, monkeypatch):
    monkeypatch.setattr(ab, "record_control_parity", lambda *a, **k: _fake("deny"))
    ok, _d = ab.enforced_decision(FakeHass(), "lock", "unlock", legacy_ok=True)
    assert ok is False


def test_legacy_denied_stays_denied(ab, monkeypatch):
    # Even if the engine would allow, a legacy denial is never loosened.
    monkeypatch.setattr(ab, "record_control_parity", lambda *a, **k: _fake("allow"))
    ok, _d = ab.enforced_decision(FakeHass(), "lock", "unlock", legacy_ok=False)
    assert ok is False


def test_non_allowlisted_capability_is_pure_legacy(ab, monkeypatch):
    # A capability outside the allowlist ignores the engine entirely, even if the
    # engine would gate it.
    monkeypatch.setattr(ab, "record_control_parity", lambda *a, **k: _fake("confirm"))
    ok, _d = ab.enforced_decision(FakeHass(), "light", "turn_on", legacy_ok=True)
    assert ok is True


def test_kill_switch_off_is_pure_legacy(ab, monkeypatch):
    monkeypatch.setattr(ab, "AUTHORITY_ENFORCE", False)
    monkeypatch.setattr(ab, "record_control_parity", lambda *a, **k: _fake("confirm"))
    ok, _d = ab.enforced_decision(FakeHass(), "lock", "unlock", legacy_ok=True)
    assert ok is True


def test_engine_fault_fails_safe_to_legacy(ab, monkeypatch):
    # A kernel fault (decision None) must never block a legitimate action.
    monkeypatch.setattr(ab, "record_control_parity", lambda *a, **k: None)
    ok, _d = ab.enforced_decision(FakeHass(), "lock", "unlock", legacy_ok=True)
    assert ok is True


def test_enforce_logs_when_it_tightens(ab, caplog, monkeypatch):
    import logging
    monkeypatch.setattr(ab, "record_control_parity", lambda *a, **k: _fake("confirm"))
    with caplog.at_level(logging.WARNING,
                         logger="custom_components.jarvis.authority_bridge"):
        ab.enforced_decision(FakeHass(), "lock", "unlock", legacy_ok=True)
    assert any("authority ENFORCE" in r.message for r in caplog.records)


# ── end-to-end against the real kernel engine ───────────────────────────────

def test_real_engine_gates_unlock_without_identity(ab):
    # With the real engine and no identity, an unlock the gate allowed is held
    # for confirmation by the belt (the engine returns CONFIRM for a security
    # capability with no authenticated identity).
    ok, decision = ab.enforced_decision(FakeHass(), "lock", "unlock", legacy_ok=True)
    assert decision is not None and decision.decision != "allow"
    assert ok is False


def test_real_engine_safe_action_unaffected(ab):
    # A non-allowlisted safe action is unchanged (pure legacy).
    ok, _d = ab.enforced_decision(FakeHass(), "light", "turn_on", legacy_ok=True)
    assert ok is True


def test_default_kill_switch_is_enforce(ab):
    assert ab.AUTHORITY_ENFORCE is True
