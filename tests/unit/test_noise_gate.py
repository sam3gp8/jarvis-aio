"""Regression tests for differential noise compensation (NoiseGate).

Stdlib-only; loaded standalone and driven by the harness FakeHass. Pins the
on-threshold, dominant-appliance attenuation, the dB floor, and None passthrough.
"""
import importlib.util
import pathlib
import sys

import pytest

from fakes import FakeHass

COMP = pathlib.Path(__file__).resolve().parents[2] / "jarvis_assistant" / "jarvis_component"


def _load_standalone(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, COMP / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ng = _load_standalone("jarvis_noise_gate", "audio/noise_gate.py")

PROFILES = {
    "sensor.dishwasher_power": 8.0,
    "sensor.microwave_power": 11.0,
}


def _gate(states: dict, **kw):
    hass = FakeHass()
    for eid, (state, attrs) in states.items():
        hass.states.set(eid, state, **attrs)
    return ng.NoiseGate(hass, PROFILES, **kw)


def test_no_appliances_running_passes_through():
    gate = _gate({"sensor.dishwasher_power": ("0", {})})
    assert gate.compensated_db(55.0) == 55.0


def test_single_appliance_attenuates_by_its_factor():
    gate = _gate({"sensor.dishwasher_power": ("1200", {})})
    assert gate.compensated_db(55.0) == 47.0  # 55 − 8


def test_dominant_appliance_sets_attenuation():
    # Both running → use the louder (microwave, 11), not the sum.
    gate = _gate({
        "sensor.dishwasher_power": ("1200", {}),
        "sensor.microwave_power": ("1500", {}),
    })
    assert gate.compensated_db(55.0) == 44.0  # 55 − 11, not 55 − 19


def test_below_threshold_not_counted():
    gate = _gate({"sensor.dishwasher_power": ("5", {})})  # < 10 W default
    assert gate.running_appliances() == []
    assert gate.compensated_db(50.0) == 50.0


def test_db_floored_at_zero():
    gate = _gate({"sensor.microwave_power": ("1500", {})})
    assert gate.compensated_db(4.0) == 0.0  # 4 − 11 → floored


def test_none_passes_through():
    gate = _gate({"sensor.microwave_power": ("1500", {})})
    assert gate.compensated_db(None) is None


def test_missing_sensor_ignored():
    gate = _gate({})  # no power sensors present at all
    assert gate.running_appliances() == []
    assert gate.compensated_db(60.0) == 60.0


def test_non_numeric_power_ignored():
    gate = _gate({"sensor.dishwasher_power": ("unavailable", {})})
    assert gate.running_appliances() == []
    assert gate.compensated_db(50.0) == 50.0


def test_custom_threshold():
    gate = _gate({"sensor.dishwasher_power": ("50", {})}, power_on_threshold=100.0)
    assert gate.running_appliances() == []  # 50 < 100
