"""Phase X shadow — energy mirrors the live draw into kernel.environment.

Observe-only: these assert the shadow logs the kernel efficiency verdict and is
fully kill-switched + defensive, and that it never changes the energy path.
"""
import logging

import pytest


@pytest.fixture
def energy(load):
    return load("energy")


def test_shadow_logs_efficiency_verdict(energy, caplog):
    st = {"watts": 9000, "peak_watts": 8000, "over_peak": True}
    with caplog.at_level(logging.DEBUG):
        energy._environment_shadow(st)
    line = [r.getMessage() for r in caplog.records if "environment(shadow)" in r.getMessage()]
    assert line, "expected an environment(shadow) log line"
    msg = line[0]
    assert "draw=9000W" in msg and "peak=8000W" in msg
    assert "kernel_over=True" in msg and "agree=True" in msg


def test_shadow_agreement_flag_tracks_incumbent(energy, caplog):
    # kernel efficiency.over (draw >= peak? no, 5000 < 8000 → False) vs incumbent
    st = {"watts": 5000, "peak_watts": 8000, "over_peak": False}
    with caplog.at_level(logging.DEBUG):
        energy._environment_shadow(st)
    msg = [r.getMessage() for r in caplog.records if "environment(shadow)" in r.getMessage()][0]
    assert "kernel_over=False" in msg and "agree=True" in msg


def test_shadow_kill_switch_silences(energy, caplog, monkeypatch):
    monkeypatch.setattr(energy, "ENVIRONMENT_SHADOW", False)
    with caplog.at_level(logging.DEBUG):
        energy._environment_shadow({"watts": 9000, "peak_watts": 8000, "over_peak": True})
    assert not [r for r in caplog.records if "environment(shadow)" in r.getMessage()]


def test_shadow_defensive_on_missing_meter(energy, caplog):
    # no whole-home meter → watts None → silently skips, never raises
    with caplog.at_level(logging.DEBUG):
        energy._environment_shadow({"watts": None, "peak_watts": 8000, "over_peak": False})
    assert not [r for r in caplog.records if "environment(shadow)" in r.getMessage()]


def test_shadow_defensive_on_zero_peak(energy):
    # a zero/absent peak must not divide-by-zero or raise
    energy._environment_shadow({"watts": 9000, "peak_watts": 0, "over_peak": True})
    energy._environment_shadow({"watts": 9000, "over_peak": True})


def test_shadow_never_raises_on_garbage(energy):
    # wrapped: any malformed input is swallowed to protect the energy path
    energy._environment_shadow({"watts": "oops", "peak_watts": "bad"})
    energy._environment_shadow({})
