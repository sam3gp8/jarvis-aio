"""MCU Phase R (R2a): freeze decision-parity — the kernel computes the pure
threshold verdict and the live SafetyManager logs it against its inline category.

LOG-ONLY: the legacy threshold still drives the freeze alert. This earns the
kernel the right to *own* the freeze verdict (the flip, R2b) only once the two
agree on real readings. R2b will fail *toward* alerting — never suppress a freeze
alert on uncertainty.
"""
import logging

import pytest


# Mirror the live constants (cognitive_core.FREEZE_*).
WARN_F = 35.0
CRIT_F = 20.0


@pytest.fixture
def sit(load):
    return load("kernel.situation")


@pytest.fixture
def hz(load):
    mod = load("hazard_situation")
    mod._last_verdict_parity = None
    return mod


# ── the pure kernel verdict ─────────────────────────────────────────────────

@pytest.mark.parametrize("temp_f,expected", [
    (-10.0, "critical"),
    (19.9, "critical"),
    (20.0, "critical"),      # boundary: <= critical
    (20.1, "warning"),
    (30.0, "warning"),
    (35.0, "warning"),       # boundary: <= warn
    (35.1, "none"),          # dead band (warn < t <= warn+5)
    (40.0, "none"),          # boundary: not yet clear
    (40.1, "clear"),         # > warn + 5 margin
    (60.0, "clear"),
    (None, "none"),          # no reading
])
def test_freeze_verdict_thresholds(sit, temp_f, expected):
    assert sit.freeze_verdict(temp_f, warn_f=WARN_F, critical_f=CRIT_F) == expected


def test_freeze_verdict_matches_legacy_inline_logic(sit):
    # Sweep a wide range and assert the kernel verdict equals the live module's
    # inline threshold category exactly (the extraction is faithful).
    def _legacy(temp_f):
        if temp_f <= CRIT_F:
            return "critical"
        if temp_f <= WARN_F:
            return "warning"
        if temp_f > WARN_F + 5:
            return "clear"
        return "none"
    t = -20.0
    while t <= 70.0:
        assert sit.freeze_verdict(t, warn_f=WARN_F, critical_f=CRIT_F) == _legacy(t), t
        t += 0.5


# ── the parity recorder ─────────────────────────────────────────────────────

def test_recorder_agrees(hz):
    kv = hz.record_freeze_verdict_parity(15.0, "critical", warn_f=WARN_F,
                                         critical_f=CRIT_F)
    assert kv == "critical"
    assert hz._last_verdict_parity == {
        "temp_f": 15.0, "legacy": "critical", "kernel": "critical", "agree": True}


def test_recorder_logs_divergence(hz, caplog):
    with caplog.at_level(logging.WARNING,
                         logger="custom_components.jarvis.hazard_situation"):
        kv = hz.record_freeze_verdict_parity(15.0, "warning", warn_f=WARN_F,
                                             critical_f=CRIT_F)
    assert kv == "critical"                          # kernel's true verdict
    assert hz._last_verdict_parity["agree"] is False
    assert any("verdict parity DIVERGENCE" in r.message for r in caplog.records)


def test_recorder_warning_band_agrees(hz):
    hz.record_freeze_verdict_parity(30.0, "warning", warn_f=WARN_F, critical_f=CRIT_F)
    assert hz._last_verdict_parity["agree"] is True
    assert hz._last_verdict_parity["kernel"] == "warning"
