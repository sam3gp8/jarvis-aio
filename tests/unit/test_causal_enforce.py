"""Phase L enforce — proactive suggestions gated on the causal ΔP verdict.

When CAUSAL_PREDICT_ENFORCE (or the `causal_predict_enforce` config key) is on, a
detected SEQUENCE pattern is only surfaced as a suggestion if the kernel causal
model confirmed it a genuine cause (`details["causal_confirms"]`). Default OFF is
behaviour-preserving; an untagged/errored pattern is treated as confirmed
(fail-safe = suggest as today). Non-safety: gates only the learned-suggestion
surface.
"""
import pytest


@pytest.fixture
def pa(load):
    return load("pattern_analyzer")


@pytest.fixture
def cfg(load, monkeypatch):
    jc = load("jarvis_config")
    store = {}
    monkeypatch.setattr(jc, "get", lambda k, d=None: store.get(k, d))
    return store


def _pat(pa, ptype="sequence", confirms=None):
    details = {}
    if confirms is not None:
        details["causal_confirms"] = confirms
    return pa.DetectedPattern(
        pattern_type=ptype, description="x then y", entity_ids=["x", "y"],
        confidence=0.9, occurrences=10, details=details)


def test_enforce_off_by_default(pa, cfg):
    assert pa._causal_predict_enforce_on() is False


def test_enforce_on_via_config(pa, cfg):
    cfg["causal_predict_enforce"] = True
    assert pa._causal_predict_enforce_on() is True


def test_enforce_on_via_flag(pa, monkeypatch):
    monkeypatch.setattr(pa, "CAUSAL_PREDICT_ENFORCE", True)
    assert pa._causal_predict_enforce_on() is True


# ── the gate decision ───────────────────────────────────────────────────────────
def test_no_gating_when_off(pa, cfg):
    # Even an explicitly-refuted sequence is NOT gated while enforce is off.
    assert pa._causal_gates_out(_pat(pa, confirms=False)) is False


def test_refuted_sequence_gated_under_enforce(pa, cfg):
    cfg["causal_predict_enforce"] = True
    assert pa._causal_gates_out(_pat(pa, confirms=False)) is True


def test_confirmed_sequence_not_gated(pa, cfg):
    cfg["causal_predict_enforce"] = True
    assert pa._causal_gates_out(_pat(pa, confirms=True)) is False


def test_untagged_sequence_failsafe_not_gated(pa, cfg):
    cfg["causal_predict_enforce"] = True
    # No verdict computed (missing tag) → fail-safe: suggest as today.
    assert pa._causal_gates_out(_pat(pa, confirms=None)) is False


def test_non_sequence_never_gated(pa, cfg):
    cfg["causal_predict_enforce"] = True
    # A time_routine with (hypothetically) a False tag is never gated — the gate
    # is sequence-only.
    p = _pat(pa, ptype="time_routine", confirms=False)
    assert pa._causal_gates_out(p) is False


def test_gate_is_defensive_on_junk(pa, cfg):
    cfg["causal_predict_enforce"] = True
    assert pa._causal_gates_out(None) is False
    assert pa._causal_gates_out(object()) is False
