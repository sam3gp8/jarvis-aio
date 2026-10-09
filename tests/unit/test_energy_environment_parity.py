"""Phase X parity — energy tracks kernel-recommender vs incumbent-offer agreement.

Observe-only: the kernel's actionable efficiency verdict (fires on 'over peak')
vs the incumbent 'would surface an offer' predicate (over peak AND >=2 sheddable
loads). These assert the agreement bookkeeping, the expected divergence, and that
it is kill-switched + defensive.
"""
import logging

import pytest


@pytest.fixture
def energy(load):
    m = load("energy")
    # counters are module-level; reset to a clean slate for each test
    m._ENV_PARITY.update(n=0, agree=0, kernel_only=0, incumbent_only=0)
    return m


def _st(watts, peak=8000, over=None, sheddable=0):
    running = [{"shed_ok": True, "name": f"load{i}"} for i in range(sheddable)]
    return {
        "watts": watts, "peak_watts": peak,
        "over_peak": (watts >= peak) if over is None else over,
        "running": running,
    }


def test_agree_when_over_peak_with_sheddable_loads(energy):
    # kernel recommends (over peak) AND incumbent offers (>=2 sheddable) -> agree
    energy._environment_parity(_st(9000, sheddable=2))
    assert energy._ENV_PARITY["n"] == 1
    assert energy._ENV_PARITY["agree"] == 1
    assert energy._ENV_PARITY["kernel_only"] == 0


def test_agree_when_under_peak(energy):
    # kernel does not recommend AND incumbent does not offer -> agree
    energy._environment_parity(_st(4000, sheddable=0))
    assert energy._ENV_PARITY["agree"] == 1


def test_kernel_only_divergence_over_peak_but_nothing_to_stagger(energy):
    # over peak (kernel recommends) but <2 sheddable (incumbent stays silent)
    energy._environment_parity(_st(9000, sheddable=1))
    assert energy._ENV_PARITY["kernel_only"] == 1
    assert energy._ENV_PARITY["agree"] == 0
    assert energy._ENV_PARITY["incumbent_only"] == 0


def test_parity_logs_running_rate(energy, caplog):
    with caplog.at_level(logging.DEBUG):
        energy._environment_parity(_st(9000, sheddable=1))   # divergence -> always logs
    msgs = [r.getMessage() for r in caplog.records if "environment(parity)" in r.getMessage()]
    assert msgs and "kernel_rec=True" in msgs[0] and "incumbent_offer=False" in msgs[0]


def test_parity_accumulates_across_calls(energy):
    energy._environment_parity(_st(9000, sheddable=2))   # agree
    energy._environment_parity(_st(4000, sheddable=0))   # agree
    energy._environment_parity(_st(9000, sheddable=1))   # kernel_only
    assert energy._ENV_PARITY["n"] == 3
    assert energy._ENV_PARITY["agree"] == 2
    assert energy._ENV_PARITY["kernel_only"] == 1


def test_parity_kill_switch(energy, monkeypatch):
    monkeypatch.setattr(energy, "ENVIRONMENT_PARITY", False)
    energy._environment_parity(_st(9000, sheddable=2))
    assert energy._ENV_PARITY["n"] == 0


def test_parity_defensive_on_missing_meter(energy):
    energy._environment_parity({"watts": None, "peak_watts": 8000, "over_peak": False})
    energy._environment_parity({"watts": 9000, "peak_watts": 0})
    energy._environment_parity({})            # no keys
    energy._environment_parity({"watts": "bad", "running": None})
    assert energy._ENV_PARITY["n"] == 0        # every malformed input skipped
