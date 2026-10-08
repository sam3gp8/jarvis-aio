"""pattern_analyzer → causal-model shadow (roadmap Phase L — shadow).

`_emit_causal_shadow` folds the detected sequence patterns into a kernel
CausalModel and logs a one-line summary, observe-only and kill-switched. These
tests pin the log line, the kill-switch, and that it never raises.
"""
import logging

import pytest


@pytest.fixture
def pa(load):
    return load("pattern_analyzer")


def _seq(pa, trigger, t_state, action, a_state, occurrences):
    """A minimal sequence DetectedPattern, as analyze() would emit."""
    return pa.DetectedPattern(
        pattern_type="sequence",
        description=f"{trigger} then {action}",
        entity_ids=[trigger, action],
        confidence=0.9,
        occurrences=occurrences,
        details={"trigger": {"entity": trigger, "state": t_state},
                 "action": {"entity": action, "state": a_state}},
    )


def test_shadow_logs_summary(pa, monkeypatch, caplog):
    monkeypatch.setattr(pa, "CAUSAL_PREDICT_SHADOW", True)
    patterns = [
        _seq(pa, "binary_sensor.front_door", "on", "light.hall", "on", 12),
        _seq(pa, "binary_sensor.dusk", "on", "light.porch", "on", 8),
        # a non-sequence pattern must be ignored
        pa.DetectedPattern(pattern_type="time_routine", description="x",
                           entity_ids=["light.x"], confidence=0.9, occurrences=9),
    ]
    with caplog.at_level(logging.DEBUG):
        pa._emit_causal_shadow(patterns)
    msgs = [r.message for r in caplog.records if "causal(shadow):" in r.message]
    assert msgs, "expected a causal(shadow) log line"
    # two sequence patterns folded; the predictor surfaces the learned effects
    assert "2 sequence pattern(s)" in msgs[0]
    assert "->" in msgs[0] and "surfaces" in msgs[0]


def test_shadow_kill_switch(pa, monkeypatch, caplog):
    monkeypatch.setattr(pa, "CAUSAL_PREDICT_SHADOW", False)
    with caplog.at_level(logging.DEBUG):
        pa._emit_causal_shadow([_seq(pa, "a", "on", "b", "on", 10)])
    assert not any("causal(shadow)" in r.message for r in caplog.records)


def test_shadow_defensive_on_junk(pa, monkeypatch, caplog):
    monkeypatch.setattr(pa, "CAUSAL_PREDICT_SHADOW", True)
    # no sequence patterns → nothing to log, but must not raise
    with caplog.at_level(logging.DEBUG):
        pa._emit_causal_shadow([])
        pa._emit_causal_shadow(None)
        # a malformed sequence (missing trigger/action) is skipped, not raised
        bad = pa.DetectedPattern(pattern_type="sequence", description="bad",
                                 entity_ids=[], confidence=0.9, occurrences=5,
                                 details={})
        pa._emit_causal_shadow([bad])
    assert not any("causal(shadow)" in r.message for r in caplog.records)