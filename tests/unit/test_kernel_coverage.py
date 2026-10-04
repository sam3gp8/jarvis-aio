"""Tests for the behavioral kernel-coverage matrix (MCU audit P0.5).

The script imports only stdlib and reads real source files, so it's exercised
directly rather than through the `jc` harness.
"""
import importlib.util
import pathlib

import pytest

_SCRIPT = (pathlib.Path(__file__).resolve().parents[2]
           / "scripts" / "kernel_coverage.py")


@pytest.fixture(scope="module")
def kc():
    spec = importlib.util.spec_from_file_location("kernel_coverage", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_scan_covers_every_path_and_contract(kc):
    rows = kc.scan()
    assert set(rows) == set(kc._PATHS)
    for contracts in rows.values():
        assert set(contracts) == set(kc._CONTRACTS)


def test_no_evidence_drift(kc):
    # Every declared non-'none' cell must have its evidence present in source.
    assert kc.drift() == []


def test_coverage_is_a_sane_percentage(kc):
    pct = kc.coverage_pct()
    assert 0.0 <= pct <= 100.0
    # Today it is deliberately low — most paths are still legacy.
    assert pct < 50.0


def test_control_device_authority_and_verify_declared(kc):
    rows = kc.scan()
    assert rows["control_device"]["authority"] == "parity"
    assert rows["control_device"]["verify"] == "full"
    # 8.30.0 (MCU Phase A): pre-action context read routes through WorldModel.
    assert rows["control_device"]["world_model"] == "parity"
    # 8.31.0 (MCU Phase A): the verify step produces a canonical ActuatorOutcome.
    assert rows["control_device"]["outcome"] == "full"
    # 8.32.0 (MCU Phase A): the actuation publishes a canonical JarvisEvent.
    assert rows["control_device"]["event"] == "parity"
    # B4b: control_device's actuation routes through the kernel planner.
    assert rows["control_device"]["plan"] == "full"
    # A path with no kernel wiring is all 'none' (friday/homer are still legacy).
    assert set(rows["friday"].values()) == {"none"}


def test_drift_detects_a_bad_declaration(kc, monkeypatch):
    # Inject a path claiming a stage with evidence that cannot be found.
    bad = dict(kc._PATHS)
    bad["phantom"] = {"module": "agent.py",
                      "contracts": {"authority": {"stage": "full",
                                                  "evidence": "__definitely_not_in_source__"}}}
    monkeypatch.setattr(kc, "_PATHS", bad)
    problems = kc.drift()
    assert any("phantom.authority" in p for p in problems)


def test_markdown_lists_paths_and_coverage(kc):
    md = kc.render_markdown()
    assert "control_device" in md and "Kernel coverage:" in md
    for contract in kc._CONTRACTS:
        assert contract in md


# ── 8.34.0 governance gate ───────────────────────────────────────────────────

def test_governance_gate_satisfied(kc):
    # Every consequential agent tool is either a declared path or allowlisted.
    assert kc.undeclared_actuator_tools() == []


def test_known_actuators_are_declared_paths(kc):
    for tool in ("control_device", "bulk_control", "execute_plan",
                 "run_scene_or_script", "set_mode"):
        assert tool in kc._PATHS


def test_gate_catches_a_new_undeclared_actuator(kc, monkeypatch):
    # A new tool that is neither a path nor allowlisted must trip the gate.
    monkeypatch.setattr(kc, "_tool_map_names",
                        lambda _src: ["control_device", "launch_missiles"])
    problems = kc.undeclared_actuator_tools()
    assert any("launch_missiles" in p for p in problems)


def test_gate_accepts_allowlisted_tool(kc, monkeypatch):
    monkeypatch.setattr(kc, "_tool_map_names",
                        lambda _src: ["get_entity_state", "control_device"])
    assert kc.undeclared_actuator_tools() == []


def test_tool_map_names_parses_agent(kc):
    # The parser finds the real tool keys without importing agent.py.
    comp = kc._component_dir()
    names = kc._tool_map_names((comp / "agent.py").read_text())
    assert "control_device" in names and "get_entity_state" in names
