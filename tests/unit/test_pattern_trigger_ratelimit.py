"""Trigger-sensor re-inclusion — the anti-flood rate limit is the centerpiece.

Motion/occupancy can be learned as triggers (opt-in), but they pulse hard —
binary_sensor.garage_motion fired 639x/day in a real log. These prove a motion
storm through the rate gate stays bounded (never re-floods the store the way the
unfiltered path did), while ordinary actuators keep their lighter cap.
"""
import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


def test_motion_storm_is_hard_capped(cc):
    cc._PATTERN_LOG_LAST.clear()
    cc._CORE.config = {"pattern_motion_min_interval": 300}
    # 1 hour of motion pulsing every 3s = 1200 pulses
    logged = sum(
        1 for i in range(1200)
        if cc._pattern_rate_ok("binary_sensor.garage_motion", 1000.0 + i * 3.0, "motion"))
    # 3600s / 300s ⇒ at most ~13 logged, vs 1200 pulses — bounded, not a flood
    assert logged <= 13, f"motion storm not capped: {logged} logged of 1200"
    assert logged >= 10, "but it should still log periodic markers, not nothing"


def test_actuator_uses_base_interval(cc):
    cc._PATTERN_LOG_LAST.clear()
    cc._CORE.config = {"pattern_log_min_interval": 60}
    # 6 minutes of a light toggling every 3s
    logged = sum(
        1 for i in range(120)
        if cc._pattern_rate_ok("light.kitchen", 2000.0 + i * 3.0, ""))
    assert 5 <= logged <= 7          # 360s / 60s ≈ 6


def test_motion_interval_much_larger_than_base(cc):
    cc._CORE.config = {"pattern_log_min_interval": 60, "pattern_motion_min_interval": 300}
    assert cc._pattern_log_interval("motion") == 300
    assert cc._pattern_log_interval("occupancy") == 300
    assert cc._pattern_log_interval("") == 60
    assert cc._pattern_log_interval("door") == 60          # doors use the base cap


def test_distinct_entities_are_independent(cc):
    cc._PATTERN_LOG_LAST.clear()
    cc._CORE.config = {"pattern_motion_min_interval": 300}
    # two motion sensors at the same instant both log (cross-entity sequences intact)
    assert cc._pattern_rate_ok("binary_sensor.hall_motion", 5000.0, "motion") is True
    assert cc._pattern_rate_ok("binary_sensor.den_motion", 5000.0, "motion") is True
    # but the same one again immediately is dropped
    assert cc._pattern_rate_ok("binary_sensor.hall_motion", 5010.0, "motion") is False


def test_motion_opt_in(cc, load, monkeypatch):
    jc = load("jarvis_config")
    store = {}
    monkeypatch.setattr(jc, "get", lambda k, d=None: store.get(k, d))
    # off by default → not learned
    assert cc._pattern_opted_in("binary_sensor.hall_motion", "motion") is False
    # opt in → motion/occupancy learned, but not an unrelated class
    store["pattern_learn_motion"] = True
    assert cc._pattern_opted_in("binary_sensor.hall_motion", "motion") is True
    assert cc._pattern_opted_in("binary_sensor.den", "occupancy") is True
    assert cc._pattern_opted_in("binary_sensor.temp", "temperature") is False
