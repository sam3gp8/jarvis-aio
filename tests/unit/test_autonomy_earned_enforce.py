"""Phase N enforce — EARNED graduated autonomy (owner-gated, opt-in, independent
of the security-ceiling switch).

The kernel plan held one Phase N rung for the owner: *replace the blanket
auto-actions flag with the EARNED per-capability level.* Atop the security
ceiling, when `autonomy_earned_enforce` is on, a granted convenience pattern
whose non-safety capability has not yet earned the top autonomy rung (ACT) from
its verified track record falls back to asking — trust is earned per capability,
not granted blanket by the mode flag.

Invariants exercised here: default OFF is behaviour-preserving; the gate can only
ever WITHHOLD auto-action, never grant it; SAFE reads stay pinned at ACT and
SECURITY is handled by the ceiling; enabling it never changes what the existing
`autonomy_enforce` (security-ceiling) switch does; and any underivable
capability / fault fails safe to acting as today.
"""
import pytest


@pytest.fixture
def act(load):
    a = load("actuation")
    a._recent_outcomes.clear()
    return a


@pytest.fixture
def cc(load, act):
    # load `act` first so cognitive_core's lazy `from . import actuation` binds the
    # same cleared/seeded instance.
    return load("cognitive_core")


@pytest.fixture
def cfg(load, monkeypatch):
    """Hermetic jarvis_config: reads resolve against a local store, never the
    host's live /config (see docs/AUDIT_2026-10.md §6)."""
    jc = load("jarvis_config")
    store = {}
    monkeypatch.setattr(jc, "get", lambda k, d=None: store.get(k, d))
    return store


@pytest.fixture
def modes_auto_on(load, monkeypatch):
    modes = load("modes")
    monkeypatch.setattr(modes, "mode_allows_auto_actions", lambda: True)
    return modes


def _seed(act, load, capability, n_success, n_fail=0):
    Outcome = load("kernel.outcome").Outcome
    for _ in range(n_success):
        act._recent_outcomes.append(Outcome(success=True, confidence=1.0, capability=capability))
    for _ in range(n_fail):
        act._recent_outcomes.append(Outcome(success=False, confidence=1.0, capability=capability))


def _granted(am, key):
    am._grants[key] = {"granted": True, "approvals": 5}


# ── the earned-grant helper (actuation) ──────────────────────────────────────

def test_earned_grant_none_for_empty_class(act):
    assert act.capability_earned_grant("") is None


def test_earned_grant_unproven_sensitive_cannot_act(act):
    # No verified record → SENSITIVE floor (SUGGEST), may_act False: autonomy is
    # EARNED, so an unproven capability has not earned the right to act.
    g = act.capability_earned_grant("climate")
    assert g is not None
    assert g.risk == "sensitive"
    assert g.may_act is False


def test_earned_grant_strong_record_earns_act(act, load):
    _seed(act, load, "climate.set_temperature", n_success=20)
    g = act.capability_earned_grant("climate")
    assert g.level == "act"
    assert g.may_act is True


def test_earned_grant_mediocre_record_stays_below_act(act, load):
    # Enough samples but a poor rate → does not reach ACT (stays CONFIRM-or-below).
    _seed(act, load, "climate.set_temperature", n_success=12, n_fail=8)
    g = act.capability_earned_grant("climate")
    assert g.may_act is False


def test_earned_grant_safe_pinned_act_without_record(act):
    # A read/announce is SAFE → pinned at ACT, acts with no record at all.
    g = act.capability_earned_grant("announce")
    assert g.pinned is True
    assert g.may_act is True


# ── the enforce switch ────────────────────────────────────────────────────────

def test_earned_enforce_off_by_default(cc):
    assert cc._autonomy_earned_enforce_on() is False


def test_earned_enforce_on_via_config(cc, cfg):
    cfg["autonomy_earned_enforce"] = True
    assert cc._autonomy_earned_enforce_on() is True


# ── the live gate ─────────────────────────────────────────────────────────────

def test_unproven_sensitive_withheld_under_earned_enforce(cc, act, cfg, modes_auto_on):
    am = cc.AutonomyManager()
    _granted(am, "hvac_eco_when_away:den")        # climate → SENSITIVE, no record
    cfg["autonomy_earned_enforce"] = True
    # Not yet earned ACT → JARVIS asks instead of auto-acting.
    assert am.is_autonomous("hvac_eco_when_away:den") is False


def test_unproven_sensitive_acts_when_earned_enforce_off(cc, act, cfg, modes_auto_on):
    am = cc.AutonomyManager()
    _granted(am, "hvac_eco_when_away:den")
    # Flag off (the default) → behaviour-preserving: still autonomous.
    assert am.is_autonomous("hvac_eco_when_away:den") is True


def test_proven_sensitive_acts_under_earned_enforce(cc, act, load, cfg, modes_auto_on):
    _seed(act, load, "climate.set_temperature", n_success=20)
    am = cc.AutonomyManager()
    _granted(am, "hvac_eco_when_away:den")
    cfg["autonomy_earned_enforce"] = True
    # Earned ACT on its verified record → auto-acts.
    assert am.is_autonomous("hvac_eco_when_away:den") is True


def test_independent_of_security_ceiling_switch(cc, act, cfg, modes_auto_on):
    # Security-ceiling enforce ON but earned-enforce OFF → an unproven SENSITIVE
    # pattern stays autonomous: enabling the ceiling switch must not silently pull
    # in earned graduation (the existing `autonomy_enforce` invariant is unchanged).
    am = cc.AutonomyManager()
    _granted(am, "hvac_eco_when_away:den")
    cfg["autonomy_enforce"] = True
    assert am.is_autonomous("hvac_eco_when_away:den") is True


def test_unknown_pattern_failsafe_acts(cc, act, cfg, modes_auto_on):
    # No derivable capability → the gate is a no-op; acts as today.
    am = cc.AutonomyManager()
    _granted(am, "totally_unknown:x")
    cfg["autonomy_earned_enforce"] = True
    assert am.is_autonomous("totally_unknown:x") is True


def test_ungranted_never_autonomous_under_earned_enforce(cc, act, cfg, modes_auto_on):
    am = cc.AutonomyManager()
    cfg["autonomy_earned_enforce"] = True
    assert am.is_autonomous("hvac_eco_when_away:den") is False   # no grant at all
