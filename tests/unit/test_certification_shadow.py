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
