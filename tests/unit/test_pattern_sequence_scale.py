"""Sequence detection: correctness + scaling.

The old detector was an O(N^2) SQL self-join with datetime()-wrapped comparisons
that defeated the index — on a large history it never finished, which stalled the
whole analyze() pass so no suggestions were ever stored. These pin the new
single-pass window: it finds real sequences, respects the 10-minute window, and
completes quickly on a large history.
"""
import sqlite3
import time
from datetime import datetime, timedelta

import pytest

_SCHEMA = """
CREATE TABLE state_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL, entity_id TEXT NOT NULL, domain TEXT NOT NULL,
    old_state TEXT, new_state TEXT NOT NULL, area_id TEXT,
    hour INTEGER, day_of_week INTEGER, triggered_by TEXT DEFAULT 'system',
    person TEXT DEFAULT 'unknown'
);
CREATE INDEX idx_sc_ts ON state_changes(timestamp);
"""


@pytest.fixture
def analyzer(load):
    return load("pattern_analyzer")


def _conn(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def _insert(conn, entity, state, dt):
    dom = entity.split(".", 1)[0]
    conn.execute(
        "INSERT INTO state_changes (timestamp, entity_id, domain, old_state, "
        "new_state, area_id, hour, day_of_week, person) VALUES (?,?,?,?,?,?,?,?,?)",
        (dt.isoformat(), entity, dom, "off", state, "", dt.hour, dt.weekday(), "unknown"))


def test_sequence_detected_within_window(analyzer, tmp_path):
    conn = _conn(tmp_path / "seq.db")
    base = datetime.now() - timedelta(days=12)
    for d in range(8):                       # 8 days: light.a on, then light.b 2 min later
        t = base + timedelta(days=d, hours=18)
        _insert(conn, "light.a", "on", t)
        _insert(conn, "light.b", "on", t + timedelta(minutes=2))
    conn.commit()
    pats = analyzer.PatternAnalyzer()._find_sequence_patterns(conn)
    m = [p for p in pats if p.pattern_type == "sequence"
         and p.details["trigger"]["entity"] == "light.a"
         and p.details["action"]["entity"] == "light.b"]
    assert m, "expected the light.a -> light.b sequence"
    assert m[0].occurrences >= 5
    assert "light.a" in m[0].description and "light.b" in m[0].description


def test_pairs_outside_window_not_counted(analyzer, tmp_path):
    conn = _conn(tmp_path / "seq2.db")
    base = datetime.now() - timedelta(days=12)
    for d in range(8):                       # 30 min apart — outside the 10-min window
        t = base + timedelta(days=d, hours=18)
        _insert(conn, "light.a", "on", t)
        _insert(conn, "light.b", "on", t + timedelta(minutes=30))
    conn.commit()
    pats = analyzer.PatternAnalyzer()._find_sequence_patterns(conn)
    m = [p for p in pats
         if p.details.get("trigger", {}).get("entity") == "light.a"
         and p.details.get("action", {}).get("entity") == "light.b"]
    assert not m


def test_sequence_scales_on_large_history(analyzer, tmp_path):
    conn = _conn(tmp_path / "big.db")
    base = datetime.now() - timedelta(days=15)
    n = 0
    for d in range(15):                       # 15 days x 1000 dense events = 15k rows
        day = base + timedelta(days=d)
        for i in range(1000):
            _insert(conn, f"switch.n{i % 30}", "on" if i % 2 else "off",
                    day + timedelta(seconds=i * 60))
            n += 1
    conn.commit()
    assert n >= 15000
    pa = analyzer.PatternAnalyzer()
    start = time.monotonic()
    pats = pa._find_sequence_patterns(conn)          # old O(N^2) join would hang here
    elapsed = time.monotonic() - start
    assert isinstance(pats, list)
    assert elapsed < 10.0, f"sequence detection too slow on 15k rows: {elapsed:.1f}s"


def test_cross_domain_sequence_with_measured_delay(analyzer, tmp_path):
    # a switch triggering a light ~90s later — cross-domain, was impossible before
    conn = _conn(tmp_path / "xd.db")
    base = datetime.now() - timedelta(days=12)
    for d in range(8):
        t = base + timedelta(days=d, hours=18)
        _insert(conn, "switch.trigger", "on", t)
        _insert(conn, "light.follower", "on", t + timedelta(seconds=90))
    conn.commit()
    pats = analyzer.PatternAnalyzer()._find_sequence_patterns(conn)
    m = [p for p in pats if p.details["trigger"]["entity"] == "switch.trigger"
         and p.details["action"]["entity"] == "light.follower"]
    assert m, "cross-domain switch->light sequence should be found"
    assert 80 <= m[0].details["delay_seconds"] <= 100   # measured, ~90s
    assert "later" in m[0].description


def test_generate_automation_uses_measured_delay(analyzer):
    import json
    pa = analyzer.PatternAnalyzer()
    p = analyzer.DetectedPattern(
        pattern_type="sequence", description="x",
        entity_ids=["switch.a", "light.b"], confidence=0.7, occurrences=8,
        details={"trigger": {"entity": "switch.a", "state": "on"},
                 "action": {"entity": "light.b", "state": "on"},
                 "delay_seconds": 90})
    auto = json.loads(pa._generate_automation(p))
    assert auto["trigger"]["platform"] == "state"
    delays = [a for a in auto["action"] if isinstance(a, dict) and "delay" in a]
    assert delays and delays[0]["delay"] == "00:01:30"   # 90s, not hardcoded 60s


def test_generate_automation_omits_tiny_delay(analyzer):
    import json
    pa = analyzer.PatternAnalyzer()
    p = analyzer.DetectedPattern(
        pattern_type="sequence", description="x",
        entity_ids=["switch.a", "light.b"], confidence=0.7, occurrences=8,
        details={"trigger": {"entity": "switch.a", "state": "on"},
                 "action": {"entity": "light.b", "state": "on"},
                 "delay_seconds": 5})
    auto = json.loads(pa._generate_automation(p))
    delays = [a for a in auto["action"] if isinstance(a, dict) and "delay" in a]
    assert not delays   # near-immediate -> no awkward tiny wait


def test_sequence_prefers_sun_condition_over_time(analyzer, tmp_path, monkeypatch):
    # With location present and the action consistently dark, the detector should
    # attach a sun condition (not a fixed time window).
    monkeypatch.setattr(analyzer, "_is_dark_at", lambda e, lat, lon: True)
    conn = _conn(tmp_path / "sun.db")
    base = datetime.now() - timedelta(days=12)
    for d in range(8):
        t = base + timedelta(days=d, hours=20)
        _insert(conn, "binary_sensor.hall_motion", "on", t)
        _insert(conn, "light.hall", "on", t + timedelta(seconds=30))
    conn.commit()
    pats = analyzer.PatternAnalyzer()._find_sequence_patterns(conn, 40.7, -74.0)
    m = [p for p in pats if p.details["trigger"]["entity"] == "binary_sensor.hall_motion"]
    assert m and m[0].details["condition"] == [{
        "condition": "sun", "after": "sunset", "before": "sunrise"}]
    assert "after dark" in m[0].description


def test_sequence_falls_back_to_time_without_location(analyzer, tmp_path):
    # No location → no sun check → the fixed time window is used instead.
    conn = _conn(tmp_path / "notloc.db")
    base = datetime.now() - timedelta(days=12)
    for d in range(8):
        t = base + timedelta(days=d, hours=20)
        _insert(conn, "binary_sensor.hall_motion", "on", t)
        _insert(conn, "light.hall", "on", t + timedelta(seconds=30))
    conn.commit()
    pats = analyzer.PatternAnalyzer()._find_sequence_patterns(conn, None, None)
    m = [p for p in pats if p.details["trigger"]["entity"] == "binary_sensor.hall_motion"]
    assert m and m[0].details["condition"][0]["condition"] == "time"
