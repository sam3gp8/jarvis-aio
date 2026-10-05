"""MCU Phase H exit-criteria gate — scripts/kernel_bypass_check.py.

Proves the gate (a) passes on the real tree — every world-mutating service call
converges on the universal seam or is classified — and (b) actually catches a new
direct device actuation, so a future bypass can't land silently.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_gate():
    spec = importlib.util.spec_from_file_location(
        "kernel_bypass_check", ROOT / "scripts" / "kernel_bypass_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate()


def test_tree_has_no_unclassified_bypasses():
    rows = gate.scan()
    violations = [r for r in rows if not r["ok"]]
    assert violations == [], (
        "world-mutating service calls outside the seam and unclassified: "
        + "; ".join(f"{r['file']}:{r['line']} {r['func']}()" for r in violations))


def test_seam_file_is_allowed():
    ok, _ = gate._classify("actuation.py",
                           {"domain": "light", "service": "turn_on", "func": "x"})
    assert ok is True


def test_communication_domains_allowed():
    for dom in ("notify", "persistent_notification", "tts", "assist_satellite"):
        ok, _ = gate._classify("whatever.py",
                               {"domain": dom, "service": "x", "func": "f"})
        assert ok is True, dom


@pytest.mark.parametrize("dom,svc", [
    ("light", "turn_on"), ("lock", "lock"), ("cover", "close_cover"),
    ("climate", "set_temperature"), ("switch", "turn_off"),
])
def test_new_direct_device_call_is_a_violation(dom, svc):
    # A new world-mutating device call in some ordinary module, not seam-routed
    # and not classified, must fail the gate.
    ok, reason = gate._classify("some_new_module.py",
                                {"domain": dom, "service": svc, "func": "_do"})
    assert ok is False and "unclassified" in reason


def test_dynamic_domain_in_unknown_site_is_a_violation():
    # domain=None (a variable) in an unclassified (file, func) is a violation —
    # forces the author to classify or seam-route.
    ok, _ = gate._classify("some_new_module.py",
                           {"domain": None, "service": None, "func": "_mystery"})
    assert ok is False
