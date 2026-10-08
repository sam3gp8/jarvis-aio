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


# ── Phase L parity: event-window contingency reconstruction ─────────────────────
def test_contingency_event_window(pa):
    # cause fires at t=0,1000,2000; effect follows within 600s only for the first
    # two (at +100, +200); the third has no effect after it.
    causes = [0.0, 1000.0, 2000.0]
    effects = [100.0, 1200.0]
    n11, n10, n01, n00 = pa._causal_contingency(causes, effects, window_s=600.0)
    assert n11 == 2 and n10 == 1          # 2 cause firings followed, 1 not
    # cause-absent bins: span 0..2000 over 600s bins → bins 0,1,2,3; cause in
    # bins 0,1,3 (0→0, 1000→1, 2000→3); effects in bins 0,2 (100→0, 1200→2).
    # cause-absent bins = {2}; it contains an effect → n01=1, n00=0.
    assert n01 == 1 and n00 == 0


def test_contingency_empty_is_total(pa):
    assert pa._causal_contingency([], [], 600.0) == (0, 0, 0, 0)
    # cause with no effect ever → all cause firings are n10, no cause-absent bins
    n11, n10, n01, n00 = pa._causal_contingency([0.0, 600.0], [], 600.0)
    assert n11 == 0 and n10 == 2


def _seed_db(rows):
    """An in-memory state_changes DB (timestamp, entity_id, new_state)."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE state_changes "
                 "(timestamp TEXT, entity_id TEXT, domain TEXT, new_state TEXT)")
    conn.executemany(
        "INSERT INTO state_changes(timestamp, entity_id, domain, new_state) "
        "VALUES (?,?,?,?)", rows)
    conn.commit()
    return conn


def test_parity_logs_and_rescores(pa, monkeypatch, caplog):
    from datetime import datetime, timedelta
    monkeypatch.setattr(pa, "CAUSAL_PREDICT_PARITY", True)
    base = datetime.now() - timedelta(days=1)

    def ts(mins):
        return (base + timedelta(minutes=mins)).isoformat()

    # door opens then hall light on, 10 times, light always within a minute;
    # the light also never comes on on its own → a genuine cause.
    rows = []
    for i in range(10):
        rows.append((ts(i * 120), "binary_sensor.door", "binary_sensor", "on"))
        rows.append((ts(i * 120 + 1), "light.hall", "light", "on"))
    conn = _seed_db(rows)

    p = _seq(pa, "binary_sensor.door", "on", "light.hall", "on", 10)
    with caplog.at_level(logging.DEBUG):
        pa._emit_causal_parity(conn, [p])
    msgs = [r.message for r in caplog.records if "causal(parity):" in r.message]
    assert msgs, "expected a causal(parity) log line"
    assert "causal-confirms=1" in msgs[0]
    assert "pattern-conf=" in msgs[0] and "causal-" in msgs[0]


def test_parity_kill_switch_and_no_seqs(pa, monkeypatch, caplog):
    conn = _seed_db([])
    monkeypatch.setattr(pa, "CAUSAL_PREDICT_PARITY", False)
    with caplog.at_level(logging.DEBUG):
        pa._emit_causal_parity(conn, [_seq(pa, "a", "on", "b", "on", 5)])
    assert not any("causal(parity)" in r.message for r in caplog.records)
    # switch on but no sequence patterns → silent
    monkeypatch.setattr(pa, "CAUSAL_PREDICT_PARITY", True)
    with caplog.at_level(logging.DEBUG):
        pa._emit_causal_parity(conn, [])
    assert not any("causal(parity)" in r.message for r in caplog.records)