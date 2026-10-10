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
