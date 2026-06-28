"""Regression tests for SpatialContextEngine occupancy fusion.

Stdlib-only; loaded standalone and driven by the harness FakeHass. Pins the
weight math, the [0,1] clamp, and the gaze+presence → skip_preamble rule.
"""
import importlib.util
import pathlib
import sys

import pytest

from fakes import FakeHass

COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"
AREA = "office"


def _load_standalone(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, COMP / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


spatial = _load_standalone("jarvis_spatial", "vision/spatial.py")


def _engine(states: dict):
    hass = FakeHass()
    for eid, (state, attrs) in states.items():
        hass.states.set(eid, state, **attrs)
    return spatial.SpatialContextEngine(hass)


def test_all_signals_sum_and_clamp_to_one():
    # 0.60 + 0.20 + 0.35 = 1.15 → clamped to 1.0
    e = _engine({
        f"sensor.{AREA}_frigate_person_count": ("2", {}),
        f"binary_sensor.{AREA}_camera_gaze_detected": ("on", {}),
        f"binary_sensor.{AREA}_mmwave_presence": ("on", {}),
    })
    out = e.evaluate(AREA)
    assert out["confidence"] == 1.0
    assert out["skip_preamble"] is True


def test_person_only_confidence():
    e = _engine({f"sensor.{AREA}_frigate_person_count": ("1", {})})
    out = e.evaluate(AREA)
    assert out["confidence"] == 0.60
    assert out["skip_preamble"] is False  # needs gaze AND presence


def test_gaze_and_presence_skip_preamble_without_person():
    e = _engine({
        f"binary_sensor.{AREA}_camera_gaze_detected": ("on", {}),
        f"binary_sensor.{AREA}_mmwave_presence": ("on", {}),
    })
    out = e.evaluate(AREA)
    assert out["confidence"] == round(0.20 + 0.35, 2)
    assert out["skip_preamble"] is True


def test_gaze_without_presence_does_not_skip():
    e = _engine({f"binary_sensor.{AREA}_camera_gaze_detected": ("on", {})})
    assert e.evaluate(AREA)["skip_preamble"] is False


def test_presence_without_gaze_does_not_skip():
    e = _engine({f"binary_sensor.{AREA}_mmwave_presence": ("on", {})})
    out = e.evaluate(AREA)
    assert out["confidence"] == 0.35
    assert out["skip_preamble"] is False


def test_no_signals_is_zero():
    out = _engine({}).evaluate(AREA)
    assert out["confidence"] == 0.0
    assert out["skip_preamble"] is False


def test_person_count_zero_contributes_nothing():
    e = _engine({f"sensor.{AREA}_frigate_person_count": ("0", {})})
    assert e.evaluate(AREA)["confidence"] == 0.0


def test_non_numeric_person_count_is_handled():
    e = _engine({f"sensor.{AREA}_frigate_person_count": ("unknown", {})})
    out = e.evaluate(AREA)
    assert out["person_count"] == 0
    assert out["confidence"] == 0.0


def test_area_id_is_scoped_to_entities():
    # Signals for a different area must not leak into this area's score.
    e = _engine({
        "sensor.kitchen_frigate_person_count": ("3", {}),
        "binary_sensor.kitchen_mmwave_presence": ("on", {}),
    })
    assert e.evaluate(AREA)["confidence"] == 0.0
