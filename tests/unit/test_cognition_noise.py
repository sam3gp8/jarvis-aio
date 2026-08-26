"""Cognition noise control — v7.49.0.

Diagnostic/technical sensors (board temps, RF signal, reactive power) must not
drive numeric-anomaly escalations — their values legitimately swing and would
flood the pipeline. And no single noisy sensor should escalate more than once
per cooldown window. Safety/access triggers are never throttled.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def cognition(load):
    c = load("cognition")
    c.reset()
    return c


class _State:
    def __init__(self, state, attributes=None):
        self.state = str(state)
        self.attributes = attributes or {}


class _Event:
    def __init__(self, entity_id, new_state, old_state=None):
        self.data = {"entity_id": entity_id, "new_state": new_state, "old_state": old_state}


def _ev(entity_id, value, dclass=None, unit=None):
    return _Event(entity_id, _State(value, {"device_class": dclass, "unit_of_measurement": unit}))


def _warm(cognition, entity_id, value, dclass=None, unit=None, n=40):
    for _ in range(n):
        cognition.process(_ev(entity_id, value, dclass, unit))


# ── technical / diagnostic exclusion ─────────────────────────────────────────
def test_hwmon_spike_not_escalated(cognition):
    eid = "sensor.home_hwmon_temperatures_core_3"
    _warm(cognition, eid, 50.0, "temperature", "°C")
    d = cognition.process(_ev(eid, 500.0, "temperature", "°C"))   # huge spike
    assert d.escalate is False
    assert "value spike" not in d.reason


def test_signal_strength_spike_not_escalated(cognition):
    eid = "sensor.garage_door_signal"
    _warm(cognition, eid, -60.0, "signal_strength", "dBm")
    d = cognition.process(_ev(eid, -5.0, "signal_strength", "dBm"))
    assert d.escalate is False


def test_reactive_power_kvar_not_escalated(cognition):
    eid = "sensor.home_energy_meter_gen5_electric_consumption_kvar"
    _warm(cognition, eid, 0.2, None, "kvar")
    d = cognition.process(_ev(eid, 9.0, None, "kvar"))
    assert d.escalate is False


def test_room_temperature_still_escalates(cognition):
    # a real room temperature (device_class temperature, but NOT diagnostic) must
    # still be eligible — proves the filter doesn't over-exclude by device_class
    eid = "sensor.living_room_temperature"
    _warm(cognition, eid, 70.0, "temperature", "°F")
    d = cognition.process(_ev(eid, 200.0, "temperature", "°F"))
    assert d.escalate is True
    assert "value spike" in d.reason


# ── per-entity anomaly cooldown ──────────────────────────────────────────────
def test_noisy_sensor_escalates_once_then_cools_down(cognition):
    eid = "sensor.kitchen_bird_count"                 # not technical
    _warm(cognition, eid, 2.0)
    d1 = cognition.process(_ev(eid, 60.0))            # first spike -> escalates
    assert d1.escalate is True and "value spike" in d1.reason
    d2 = cognition.process(_ev(eid, 80.0))            # immediate second spike
    assert d2.escalate is False                        # suppressed by cooldown


def test_cooldown_is_per_entity(cognition):
    _warm(cognition, "sensor.bird_a", 2.0)
    _warm(cognition, "sensor.bird_b", 2.0)
    a = cognition.process(_ev("sensor.bird_a", 60.0))
    b = cognition.process(_ev("sensor.bird_b", 60.0))
    assert a.escalate is True and b.escalate is True   # different entities, both allowed


def test_cooldown_expires(cognition, monkeypatch):
    import time as _t
    base = _t.time()
    monkeypatch.setattr(cognition.time, "time", lambda: base)
    eid = "sensor.kitchen_bird_count"
    _warm(cognition, eid, 2.0)
    assert cognition.process(_ev(eid, 60.0)).escalate is True
    assert cognition.process(_ev(eid, 80.0)).escalate is False   # within cooldown
    monkeypatch.setattr(cognition.time, "time",
                        lambda: base + cognition.ANOMALY_COOLDOWN + 1)
    assert cognition.process(_ev(eid, 90.0)).escalate is True     # cooldown expired


# ── safety is never throttled ────────────────────────────────────────────────
def test_safety_bypasses_cooldown(cognition):
    d1 = cognition.process(_ev("binary_sensor.kitchen_smoke", "on", "smoke"))
    d2 = cognition.process(_ev("binary_sensor.hall_smoke", "on", "smoke"))
    d3 = cognition.process(_ev("binary_sensor.kitchen_smoke", "on", "smoke"))
    assert d1.escalate and d2.escalate and d3.escalate
    assert "safety" in d1.reason
