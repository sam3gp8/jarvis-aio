"""Phase AE enforce — the certification gate forbids a silent un-wiring.

scripts/kernel_certification_check.py is the CI teeth of Phase AE: it fails the
build if any of the eight observable scenario classes loses its live observer, if
the dashboard accessor disappears, or if either of the two deliberately
unobservable classes gets falsely wired. These tests run the gate against the real
tree (it must pass) and confirm it actually catches a removed observer and a faked
closure.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
GATE = ROOT / "scripts" / "kernel_certification_check.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("kernel_certification_check", GATE)
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


def test_gate_catches_a_removed_observer(monkeypatch):
    # If identity.py stopped emitting the security observation, the gate must fail.
    gate = _load_gate()
    real = pathlib.Path.read_text

    def _fake_read(self, *a, **k):
        txt = real(self, *a, **k)
        if self.name == "identity.py":
            txt = txt.replace("CERT.SECURITY", "CERT.NONE")  # drop the marker
        return txt

    monkeypatch.setattr(pathlib.Path, "read_text", _fake_read)
    assert gate.main(["--check"]) == 1


def test_gate_catches_a_faked_closure(monkeypatch):
    # If an owner module faked an observation for an unobservable class, the
    # honesty invariant must fail — a hollow closure cannot be slipped in.
    gate = _load_gate()
    real = pathlib.Path.read_text

    def _fake_read(self, *a, **k):
        txt = real(self, *a, **k)
        if self.name == "cognitive_core.py":
            txt = txt + "\n# certification_observe(CERT.CONFLICTING_PRIORITIES)\n"
        return txt

    monkeypatch.setattr(pathlib.Path, "read_text", _fake_read)
    assert gate.main(["--check"]) == 1


def test_gate_catches_a_downgraded_stage(monkeypatch):
    # If the adoption stage were rolled back below enforce, the gate must fail.
    gate = _load_gate()
    real_checks = gate._checks   # capture BEFORE patching

    def _fake_checks():
        base = [c for c in real_checks()
                if c[0] != "certification declared enforce"]
        return base + [("certification declared enforce", False, "stage=parity")]

    monkeypatch.setattr(gate, "_checks", _fake_checks)
    assert gate.main(["--check"]) == 1
