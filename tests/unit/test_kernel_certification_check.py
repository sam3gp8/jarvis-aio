"""Phase AE enforce — the certification gate forbids a silent un-wiring.

scripts/kernel_certification_check.py is the CI teeth of Phase AE: it fails the
build if any observable scenario class loses its live observer, if the dashboard
accessor disappears, or if a class listed as unobservable gets falsely wired. All
ten classes are now observed (the unobservable list is empty), so the honesty
mechanism is exercised by marking a genuinely-wired class unobservable and
confirming the gate flags it. These tests run the gate against the real tree (it
must pass) and confirm it catches a removed observer and a faked closure.
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
    # The honesty invariant: a class listed as unobservable must NOT be wired.
    # Every class is now observed, so _UNOBSERVED is empty — prove the mechanism
    # still fires by marking a genuinely-wired class (conflicting_priorities, live
    # in cognitive_core.py) as unobservable. The gate must then flag it as falsely
    # wired, so a hollow closure can never be slipped past the honesty check.
    gate = _load_gate()
    monkeypatch.setattr(gate, "_UNOBSERVED", ("CONFLICTING_PRIORITIES",))
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
