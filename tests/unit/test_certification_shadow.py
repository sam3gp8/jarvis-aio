"""Phase AE shadow — cognitive_core folds each live pass into the certification
ledger as a `proactive` scenario observation.

The integration loop's perceive→predict→decide→act→learn IS the proactive
scenario's shape, so `_emit_certification_shadow` observes the proactive class
(closed when the loop closed). Observe-only, kill-switched, never raises into the
tick. These tests pin the gate (prediction required), the closed mapping, and the
kill-switch; the ledger's own derivations are covered in test_kernel_certification.
"""
import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


@pytest.fixture(autouse=True)
def _fresh_ledger_and_config(cc):
    """Reset the module-level ledger and isolate _CORE.config per test."""
    saved_cfg = cc._CORE.config
    saved_flag = cc.CERTIFICATION_SHADOW
    saved_ledger = cc._CERT_LEDGER
    cc._CORE.config = {}
    cc._CERT_LEDGER = None
    cc.CERTIFICATION_SHADOW = True
    yield
    cc._CORE.config = saved_cfg
    cc.CERTIFICATION_SHADOW = saved_flag
    cc._CERT_LEDGER = saved_ledger


def test_closed_pass_records_a_closed_proactive(cc):
    cc._emit_certification_shadow(perceived=True, predicted=True, decided=True,
                                  acted=True, learned=True)
    led = cc._CERT_LEDGER
    assert led is not None and led.observations == 1
    rep = led.report()
    assert rep.closed_classes == ("proactive",)


def test_open_pass_records_exercised_not_closed(cc):
    # predicted but the loop did not run to act+learn → proactive exercised, open
    cc._emit_certification_shadow(perceived=True, predicted=True, decided=True,
                                  acted=False, learned=False)
    rep = cc._CERT_LEDGER.report()
    assert rep.exercised_classes == ("proactive",)
    assert rep.closed_classes == ()


def test_no_prediction_is_not_observed(cc):
    # a bare perceive with no anticipation is not a proactive scenario
    cc._emit_certification_shadow(perceived=True, predicted=False, decided=False,
                                  acted=False, learned=False)
    assert cc._CERT_LEDGER is None  # nothing recorded, ledger never created


def test_sticky_best_across_passes(cc):
    cc._emit_certification_shadow(perceived=True, predicted=True, decided=True,
                                  acted=True, learned=True)       # closed
    cc._emit_certification_shadow(perceived=True, predicted=True, decided=False,
                                  acted=False, learned=False)     # later open
    led = cc._CERT_LEDGER
    assert led.observations == 2
    assert led.report().closed_classes == ("proactive",)         # stays closed


def test_kill_switch_module_flag(cc):
    cc.CERTIFICATION_SHADOW = False
    cc._emit_certification_shadow(perceived=True, predicted=True, decided=True,
                                  acted=True, learned=True)
    assert cc._CERT_LEDGER is None


def test_kill_switch_config_key(cc):
    cc._CORE.config = {"certification_shadow": False}
    cc._emit_certification_shadow(perceived=True, predicted=True, decided=True,
                                  acted=True, learned=True)
    assert cc._CERT_LEDGER is None


def test_never_raises_on_bad_ledger(cc):
    # defensive: even if observe() blows up, the error is swallowed, not propagated
    class _Boom:
        def observe(self, *a, **k):
            raise RuntimeError("boom")
    cc._CERT_LEDGER = _Boom()
    # should swallow the error, not propagate into the tick
    cc._emit_certification_shadow(perceived=True, predicted=True, decided=True,
                                  acted=True, learned=True)


# ── the shared observer (certification_observe) ──────────────────────────────
def test_observe_routes_into_the_shared_ledger(cc):
    from jc.kernel import certification as CERT
    cc.certification_observe(CERT.CONVERSATIONAL, closed_loop=True)
    cc.certification_observe(CERT.PROACTIVE, closed_loop=False)
    rep = cc.certification_ledger().report()
    assert set(rep.exercised_classes) == {"conversational", "proactive"}
    assert rep.closed_classes == ("conversational",)


def test_observe_kill_switch(cc):
    cc.CERTIFICATION_SHADOW = False
    from jc.kernel import certification as CERT
    cc.certification_observe(CERT.CONVERSATIONAL, closed_loop=True)
    assert cc._CERT_LEDGER is None


# ── agent.py conversational wiring (shared ledger) ───────────────────────────
@pytest.fixture
def agent(load):
    return load("agent")


def test_agent_conversational_closes_with_action_and_answer(cc, agent):
    agent._emit_conversational_shadow(acted=True, answered=True)
    rep = cc.certification_ledger().report()
    assert rep.closed_classes == ("conversational",)


def test_agent_conversational_open_without_action(cc, agent):
    agent._emit_conversational_shadow(acted=False, answered=True)
    rep = cc.certification_ledger().report()
    assert rep.exercised_classes == ("conversational",)
    assert rep.closed_classes == ()


def test_agent_and_core_share_one_ledger(cc, agent):
    # the agent's conversational observation and the core's proactive observation
    # land in the SAME ledger — one system-wide certification dashboard
    cc._emit_certification_shadow(perceived=True, predicted=True, decided=True,
                                  acted=True, learned=True)        # proactive closed
    agent._emit_conversational_shadow(acted=True, answered=True)   # conversational closed
    rep = cc.certification_ledger().report()
    assert set(rep.closed_classes) == {"proactive", "conversational"}
    assert cc._CERT_LEDGER.observations == 2


def test_agent_delegation_closes_on_success(cc, agent):
    agent._emit_delegation_shadow(succeeded=True)
    assert cc.certification_ledger().report().closed_classes == ("delegation",)


def test_agent_delegation_open_on_failure(cc, agent):
    agent._emit_delegation_shadow(succeeded=False)
    rep = cc.certification_ledger().report()
    assert rep.exercised_classes == ("delegation",)
    assert rep.closed_classes == ()


def test_agent_provider_failure_closes_on_recovery(cc, agent):
    agent._emit_provider_failure_shadow(recovered=True)
    assert cc.certification_ledger().report().closed_classes == ("provider_failure",)


def test_agent_provider_failure_open_when_both_fail(cc, agent):
    agent._emit_provider_failure_shadow(recovered=False)
    rep = cc.certification_ledger().report()
    assert rep.exercised_classes == ("provider_failure",)
    assert rep.closed_classes == ()


def test_agent_emitters_kill_switched(cc, agent):
    cc.CERTIFICATION_SHADOW = False
    agent._emit_delegation_shadow(succeeded=True)
    agent._emit_provider_failure_shadow(recovered=True)
    agent._emit_conversational_shadow(acted=True, answered=True)
    assert cc._CERT_LEDGER is None


# ── actuation `failure` + continuity `restart` (shared ledger) ───────────────
@pytest.fixture
def actuation(load):
    return load("actuation")


@pytest.fixture
def continuity(load):
    return load("continuity")


def test_actuation_failure_closes_on_recovery(cc, actuation):
    actuation._emit_failure_shadow(recovered=True)
    assert cc.certification_ledger().report().closed_classes == ("failure",)


def test_actuation_failure_open_when_unrecovered(cc, actuation):
    actuation._emit_failure_shadow(recovered=False)
    rep = cc.certification_ledger().report()
    assert rep.exercised_classes == ("failure",)
    assert rep.closed_classes == ()


def test_continuity_restart_closes_on_resume(cc, continuity):
    continuity._emit_restart_shadow(resumed=True)
    assert cc.certification_ledger().report().closed_classes == ("restart",)


def test_continuity_restart_open_when_nothing_live(cc, continuity):
    continuity._emit_restart_shadow(resumed=False)
    rep = cc.certification_ledger().report()
    assert rep.exercised_classes == ("restart",)
    assert rep.closed_classes == ()


def test_six_classes_share_one_ledger(cc, agent, actuation, continuity):
    # all six live surfaces fold into the SAME system-wide certification dashboard
    cc._emit_certification_shadow(perceived=True, predicted=True, decided=True,
                                  acted=True, learned=True)        # proactive
    agent._emit_conversational_shadow(acted=True, answered=True)   # conversational
    agent._emit_delegation_shadow(succeeded=True)                  # delegation
    agent._emit_provider_failure_shadow(recovered=True)            # provider_failure
    actuation._emit_failure_shadow(recovered=True)                 # failure
    continuity._emit_restart_shadow(resumed=True)                  # restart
    rep = cc.certification_ledger().report()
    assert set(rep.closed_classes) == {
        "proactive", "conversational", "delegation", "provider_failure",
        "failure", "restart"}
    assert len(rep.closed_classes) == 6
    assert cc._CERT_LEDGER.observations == 6


# ── identity `security` + continuity `long_horizon` ──────────────────────────
@pytest.fixture
def identity(load):
    return load("identity")


# ── parity dashboard (certification_report / status) ─────────────────────────
def test_certification_report_zero_before_observations(cc):
    d = cc.certification_report()
    assert d["observations"] == 0
    assert d["is_certified"] is False
    assert d["coverage"] == 0.0
    assert d["closed"] == []


def test_certification_report_reflects_observations(cc):
    from jc.kernel import certification as CERT
    cc.certification_observe(CERT.PROACTIVE, closed_loop=True)
    cc.certification_observe(CERT.CONVERSATIONAL, closed_loop=False)
    d = cc.certification_report()
    assert d["observations"] == 2
    # proactive closed; conversational exercised-but-open
    assert d["closed"] == ["proactive"]
    assert set(d["exercised"]) == {"proactive", "conversational"}
    assert d["is_certified"] is False


def test_status_includes_certification_dashboard(cc):
    st = cc.status()
    assert "certification" in st
    assert "coverage" in st["certification"]
    assert "observations" in st["certification"]


def test_identity_security_confirm_closes(cc, identity):
    identity._emit_security_shadow(confirmed=True)
    assert cc.certification_ledger().report().closed_classes == ("security",)


def test_identity_security_deny_also_closes(cc, identity):
    # a deliberate deny is a correct security outcome — it closes the loop too
    identity._emit_security_shadow(confirmed=False)
    assert cc.certification_ledger().report().closed_classes == ("security",)


def test_continuity_long_horizon_closes_on_resume(cc, continuity):
    continuity._emit_long_horizon_shadow(resumed=True)
    assert cc.certification_ledger().report().closed_classes == ("long_horizon",)


def test_continuity_long_horizon_open_when_not_resumed(cc, continuity):
    continuity._emit_long_horizon_shadow(resumed=False)
    rep = cc.certification_ledger().report()
    assert rep.exercised_classes == ("long_horizon",)
    assert rep.closed_classes == ()


def test_eight_observable_classes_share_one_ledger(
        cc, agent, actuation, continuity, identity):
    # every honestly-observable class folds into ONE system-wide dashboard (8/10)
    cc._emit_certification_shadow(perceived=True, predicted=True, decided=True,
                                  acted=True, learned=True)        # proactive
    agent._emit_conversational_shadow(acted=True, answered=True)   # conversational
    agent._emit_delegation_shadow(succeeded=True)                  # delegation
    agent._emit_provider_failure_shadow(recovered=True)            # provider_failure
    actuation._emit_failure_shadow(recovered=True)                 # failure
    continuity._emit_restart_shadow(resumed=True)                  # restart
    continuity._emit_long_horizon_shadow(resumed=True)             # long_horizon
    identity._emit_security_shadow(confirmed=True)                 # security
    rep = cc.certification_ledger().report()
    assert set(rep.closed_classes) == {
        "proactive", "conversational", "delegation", "provider_failure",
        "failure", "restart", "long_horizon", "security"}
    assert len(rep.closed_classes) == 8
    # the two not-yet-observable classes remain missing
    assert set(rep.missing_classes) == {"conflicting_priorities", "cognitive_error"}
    assert rep.is_certified is False
