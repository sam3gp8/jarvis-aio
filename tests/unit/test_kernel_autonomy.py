"""Kernel Phase N — graduated autonomy (per-capability trust), pure primitive.

The autonomy level is a pure function of a capability's verified track record and
its risk class. These pin the ladder, the risk ceilings/floors, the earning
thresholds, and the bounded/reversible transition — the governance invariants the
owner-gated enforce rung will depend on."""
import pytest


@pytest.fixture
def autonomy(load):
    return load("kernel.autonomy")


@pytest.fixture
def outcome(load):
    return load("kernel.outcome")


def _stats(outcome, n, successes):
    """An OutcomeStats with ``n`` samples and ``successes`` wins."""
    return outcome.OutcomeStats(count=n, successes=successes)


# ── ladder + risk class pinning ───────────────────────────────────────────────

def test_safe_capability_is_pinned_at_act(autonomy, outcome):
    # a read/announce needs no track record — always ACT.
    g = autonomy.grant("announce", _stats(outcome, 0, 0))
    assert g.risk == "safe"
    assert g.level == autonomy.ACT
    assert g.pinned is True and g.may_act is True


def test_security_capability_pinned_at_confirm_even_with_perfect_record(autonomy, outcome):
    # a flawless streak can NEVER earn a security capability the right to act.
    g = autonomy.grant("lock.unlock", _stats(outcome, 1000, 1000))
    assert g.risk == "security"
    assert g.level == autonomy.CONFIRM
    assert g.pinned is True
    assert g.may_act is False and g.needs_confirmation is True


def test_alarm_disarm_is_security_pinned(autonomy, outcome):
    g = autonomy.grant("alarm_control_panel.alarm_disarm", _stats(outcome, 500, 500))
    assert g.risk == "security" and g.level == autonomy.CONFIRM


# ── sensitive actuation earns up ──────────────────────────────────────────────

def test_sensitive_with_no_record_holds_at_suggest(autonomy, outcome):
    g = autonomy.grant("light.turn_on", _stats(outcome, 0, 0))
    assert g.risk == "sensitive"
    assert g.level == autonomy.SUGGEST and g.suggest_only is True


def test_sensitive_earns_confirm_on_a_decent_record(autonomy, outcome):
    # >= MIN_SAMPLES_CONFIRM at >= RATE_CONFIRM, but short of the ACT bar.
    g = autonomy.grant("light.turn_on", _stats(outcome, 6, 4))  # 0.67 success
    assert g.level == autonomy.CONFIRM


def test_sensitive_earns_act_on_a_strong_record(autonomy, outcome):
    g = autonomy.grant("switch.turn_on", _stats(outcome, 25, 25))  # 1.0 success
    assert g.level == autonomy.ACT and g.may_act is True


def test_sensitive_with_enough_samples_but_poor_rate_stays_low(autonomy, outcome):
    # 25 samples but only 0.5 success → below RATE_CONFIRM → floor.
    g = autonomy.grant("cover.open_cover", _stats(outcome, 25, 12))
    assert g.level == autonomy.SUGGEST


# ── bounded + reversible transitions ──────────────────────────────────────────

def test_transition_climbs_at_most_one_rung(autonomy, outcome):
    # strong record would earn ACT, but from SUGGEST we may only step to CONFIRM.
    g = autonomy.grant("light.turn_on", _stats(outcome, 30, 30), current=autonomy.SUGGEST)
    assert g.level == autonomy.CONFIRM


def test_transition_demotes_at_most_one_rung(autonomy, outcome):
    # record collapsed to the floor; from ACT we may only step down to CONFIRM.
    g = autonomy.grant("light.turn_on", _stats(outcome, 0, 0), current=autonomy.ACT)
    assert g.level == autonomy.CONFIRM


def test_step_toward_is_single_rung(autonomy):
    assert autonomy.step_toward(autonomy.SUGGEST, autonomy.ACT) == autonomy.CONFIRM
    assert autonomy.step_toward(autonomy.ACT, autonomy.SUGGEST) == autonomy.CONFIRM
    assert autonomy.step_toward(autonomy.CONFIRM, autonomy.CONFIRM) == autonomy.CONFIRM


def test_security_current_above_ceiling_is_clamped_down(autonomy, outcome):
    # defensive: even if fed a bogus current=ACT, a security cap returns to CONFIRM.
    g = autonomy.grant("lock.unlock", _stats(outcome, 10, 10), current=autonomy.ACT)
    assert g.level == autonomy.CONFIRM


# ── earned_level + totality ───────────────────────────────────────────────────

def test_earned_level_none_stats_is_floor(autonomy):
    assert autonomy.earned_level(None, "sensitive") == autonomy.SUGGEST
    assert autonomy.earned_level(None, "safe") == autonomy.ACT
    assert autonomy.earned_level(None, "security") == autonomy.CONFIRM


def test_unknown_capability_treated_as_sensitive(autonomy, outcome):
    # authority.sensitivity() maps unknown → SENSITIVE (never silently safe).
    g = autonomy.grant("frobnicate.wibble", _stats(outcome, 0, 0))
    assert g.risk == "sensitive" and g.level == autonomy.SUGGEST


def test_grant_to_dict_roundtrips_fields(autonomy, outcome):
    g = autonomy.grant("light.turn_on", _stats(outcome, 25, 25))
    d = g.to_dict()
    assert d["capability"] == "light.turn_on"
    assert d["level"] == autonomy.ACT
    assert d["risk"] == "sensitive"
    assert d["samples"] == 25 and d["success_rate"] == 1.0
