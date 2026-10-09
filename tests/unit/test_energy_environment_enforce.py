"""Phase X enforce — the kernel environment recommender owns the over-peak
verdict that gates a proactive energy offer, when the owner-gated flip is on.

Default OFF (behaviour-preserving: returns the legacy ``over_peak`` untouched);
on via the module flag or the ``environment_enforce`` config key the kernel's
actionable efficiency verdict wins; fail-safe to legacy on any bad input.
"""
import pytest


@pytest.fixture
def energy(load):
    return load("energy")


@pytest.fixture
def set_enforce(load, monkeypatch):
    """Turn the enforce flip on/off via the config key (not the module flag)."""
    jc = load("jarvis_config")

    def _set(on):
        monkeypatch.setattr(jc, "get", lambda k, d=None: on if k == "environment_enforce" else d)
    return _set


def _st(watts, peak=8000, over=None):
    return {
        "watts": watts, "peak_watts": peak,
        "over_peak": (watts >= peak) if over is None else over,
    }


def test_default_off_returns_legacy_over_peak(energy):
    # Shipping default: the helper echoes the legacy flag verbatim, even when it
    # contradicts the raw draw — proving enforce is off and nothing recomputes.
    assert energy._environment_over_peak(_st(9000, over=False)) is False
    assert energy._environment_over_peak(_st(4000, over=True)) is True


def test_enforce_on_kernel_verdict_wins(energy, set_enforce):
    set_enforce(True)
    # Over peak by draw but legacy flag says False → kernel verdict (True) wins.
    assert energy._environment_over_peak(_st(9000, over=False)) is True
    # Under peak → kernel verdict False, overriding a stale legacy True.
    assert energy._environment_over_peak(_st(4000, over=True)) is False


def test_enforce_via_module_flag(energy, monkeypatch):
    monkeypatch.setattr(energy, "ENVIRONMENT_ENFORCE", True)
    assert energy._environment_enforce_on() is True
    assert energy._environment_over_peak(_st(9000, over=False)) is True


def test_enforce_off_by_default(energy):
    assert energy._environment_enforce_on() is False


def test_enforce_fail_safe_on_bad_meter(energy, set_enforce):
    set_enforce(True)
    # No usable meter reading → fall back to the legacy flag, never raise.
    assert energy._environment_over_peak({"over_peak": True}) is True
    assert energy._environment_over_peak({"watts": None, "peak_watts": 8000, "over_peak": False}) is False
    assert energy._environment_over_peak({"watts": 9000, "peak_watts": 0, "over_peak": True}) is True
    assert energy._environment_over_peak({"watts": "bad", "peak_watts": 8000, "over_peak": False}) is False
