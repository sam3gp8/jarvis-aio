"""Phase R enforce — the integration gate forbids open-loop regressions.

scripts/kernel_integration_check.py is the CI teeth of Phase R: it fails the
build if the perceive→predict→decide→act→learn loop stops being assembled from
the live tick. These tests run the gate against the real tree (it must pass) and
confirm it actually catches a missing invariant.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
GATE = ROOT / "scripts" / "kernel_integration_check.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("kernel_integration_check", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gate_passes_on_the_real_tree():
    gate = _load_gate()
    assert gate.main(["--check"]) == 0


def test_all_invariants_green():
    gate = _load_gate()
    checks = gate._checks()
    assert checks, "gate produced no invariants"
    assert all(ok for _name, ok, _detail in checks)


def test_gate_catches_a_missing_signal(monkeypatch):
    # If cognitive_core stopped supplying a stage signal, the gate must fail.
    gate = _load_gate()
    real = pathlib.Path.read_text

    def _fake_read(self, *a, **k):
        txt = real(self, *a, **k)
        if self.name == "cognitive_core.py":
            txt = txt.replace("learned=", "learnedXX=")   # break the ACT/LEARN wiring
        return txt

    monkeypatch.setattr(pathlib.Path, "read_text", _fake_read)
    assert gate.main(["--check"]) == 1


def test_gate_catches_a_downgraded_stage(monkeypatch):
    # If the adoption stage were rolled back below enforce, the gate must fail.
    gate = _load_gate()
    real_checks = gate._checks   # capture BEFORE patching

    def _fake_checks():
        base = [c for c in real_checks() if c[0] != "integration declared enforce"]
        return base + [("integration declared enforce", False, "stage=parity")]

    monkeypatch.setattr(gate, "_checks", _fake_checks)
    assert gate.main(["--check"]) == 1
