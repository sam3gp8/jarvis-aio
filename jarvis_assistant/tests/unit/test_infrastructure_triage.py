"""Regression tests for the InfrastructureTriage health aggregator.

Loaded standalone (stdlib-only) and driven by the harness FakeHass. Pins the
thresholds, the offline-vs-unreadable distinction, multi-fault ordering, and the
shape of the returned verdict.
"""
import importlib.util
import pathlib
import sys

import pytest

from fakes import FakeHass  # provided on sys.path by tests/conftest.py

COMP = pathlib.Path(__file__).resolve().parents[2] / "jarvis_assistant" / "jarvis_component"


def _load_standalone(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, COMP / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


monitor = _load_standalone("jarvis_monitor", "diagnostics/monitor.py")


def _verdict(states: dict) -> dict:
    hass = FakeHass()
    for eid, (state, attrs) in states.items():
        hass.states.set(eid, state, **attrs)
    return monitor.InfrastructureTriage(hass, honorific="sir").evaluate()


_HEALTHY = {
    "sensor.server_root_storage_usage": ("55", {}),
    "sensor.server_ram_usage": ("60", {}),
    "binary_sensor.core_switch_status": ("on", {}),
    "binary_sensor.basement_freeze_sensor_connectivity": ("on", {}),
}


def test_all_healthy_is_silent():
    assert _verdict(_HEALTHY) == {
        "alert_required": False, "message": "", "critical": False, "tags": [],
    }


def test_verdict_carries_finding_tags():
    v = _verdict({
        **_HEALTHY,
        "sensor.server_root_storage_usage": ("97", {}),
        "binary_sensor.basement_freeze_sensor_connectivity": ("off", {}),
    })
    assert "root storage" in v["tags"]
    assert any("freeze" in t for t in v["tags"])


def test_root_cause_power_loss_when_switch_unpowered():
    v = _verdict({
        **_HEALTHY,
        "binary_sensor.core_switch_status": ("off", {}),
        "sensor.core_switch_power_watts": ("0", {}),
    })
    assert v["critical"] is True
    assert "power loss" in v["message"].lower()
    assert "watts" in v["message"].lower()


def test_root_cause_link_fault_when_switch_still_powered():
    v = _verdict({
        **_HEALTHY,
        "binary_sensor.core_switch_status": ("off", {}),
        "sensor.core_switch_power_watts": ("42", {}),
    })
    assert v["critical"] is True
    msg = v["message"].lower()
    assert "network or uplink fault" in msg
    assert "42 watts" in msg


def test_root_cause_monitor_unreachable():
    v = _verdict({
        **_HEALTHY,
        "binary_sensor.core_switch_status": ("off", {}),
        "sensor.core_switch_power_watts": ("unavailable", {}),
    })
    assert v["critical"] is True
    assert "power monitor is unreachable" in v["message"].lower()


def test_root_cause_absent_when_switch_healthy():
    # No core-switch fault → no power-cause clause appended.
    v = _verdict({**_HEALTHY, "sensor.server_root_storage_usage": ("97", {})})
    assert "watts" not in v["message"].lower()


def test_storage_warning_band():
    v = _verdict({**_HEALTHY, "sensor.server_root_storage_usage": ("92", {})})
    assert v["alert_required"] is True
    assert v["critical"] is False   # 92 is > warn(90) but not > critical(96)
    assert "storage" in v["message"].lower()


def test_storage_critical_band():
    v = _verdict({**_HEALTHY, "sensor.server_root_storage_usage": ("97", {})})
    assert v["critical"] is True
    assert "critically" in v["message"].lower()


def test_ram_over_threshold_is_critical():
    v = _verdict({**_HEALTHY, "sensor.server_ram_usage": ("93", {})})
    assert v["alert_required"] is True
    assert v["critical"] is True


def test_core_switch_off_is_critical():
    v = _verdict({**_HEALTHY, "binary_sensor.core_switch_status": ("off", {})})
    assert v["critical"] is True
    assert "switch" in v["message"].lower()


def test_freeze_sensor_offline_is_critical():
    v = _verdict({**_HEALTHY, "binary_sensor.basement_freeze_sensor_connectivity": ("off", {})})
    assert v["critical"] is True
    assert "freeze" in v["message"].lower()


def test_missing_sensor_is_warning_not_critical():
    states = dict(_HEALTHY)
    del states["sensor.server_root_storage_usage"]  # entity entirely absent
    v = _verdict(states)
    assert v["alert_required"] is True
    assert v["critical"] is False
    assert "can't read" in v["message"].lower() or "unavailable" in v["message"].lower()


def test_unavailable_state_is_warning():
    v = _verdict({**_HEALTHY, "binary_sensor.core_switch_status": ("unavailable", {})})
    assert v["alert_required"] is True
    assert v["critical"] is False   # offline-visibility, not a confirmed fault


def test_non_numeric_storage_is_handled():
    v = _verdict({**_HEALTHY, "sensor.server_root_storage_usage": ("n/a", {})})
    assert v["alert_required"] is True
    assert v["critical"] is False


def test_multiple_faults_lead_with_critical():
    v = _verdict({
        "sensor.server_root_storage_usage": ("97", {}),   # critical
        "sensor.server_ram_usage": ("50", {}),            # ok
        "binary_sensor.core_switch_status": ("on", {}),
        "binary_sensor.basement_freeze_sensor_connectivity": ("off", {}),  # critical
    })
    assert v["critical"] is True
    # Both critical findings present in one synthesised sentence.
    assert "storage" in v["message"].lower()
    assert "freeze" in v["message"].lower()
    assert v["message"].count(".") >= 1
