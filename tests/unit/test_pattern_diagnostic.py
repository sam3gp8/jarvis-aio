"""pattern_diagnostic(): busiest sources + near-miss routine candidates.

Turns "0 found" into a why — a candidate seen on almost enough days, or a
flooding source — so the next fix is data-driven rather than guessed.
"""
import sqlite3
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
    c = sqlite3.connect(str(path)); c.executescript(_SCHEMA); c.commit()
    c.row_factory = sqlite3.Row; return c


def _ins(conn, entity, state, dt):
    dom = entity.split(".", 1)[0]
    conn.execute("INSERT INTO state_changes (timestamp, entity_id, domain, old_state,"
                 " new_state, area_id, hour, day_of_week, person) VALUES (?,?,?,?,?,?,?,?,?)",
                 (dt.isoformat(), entity, dom, "off", state, "", dt.hour, dt.weekday(), "unknown"))


def test_diagnostic_reports_sources_and_candidates(analyzer, tmp_path):
    db = tmp_path / "d.db"
    conn = _conn(db)
    base = datetime.now() - timedelta(days=12)
    # a near-miss routine: light.porch 'on' at a FIXED 18:00 on 5 distinct days
    ref = base.replace(hour=18, minute=0, second=0, microsecond=0)
    for d in range(5):
        _ins(conn, "light.porch", "on", ref + timedelta(days=d))
    # a flooding source: media_player churn, many changes, scattered hours
    for i in range(60):
        _ins(conn, "media_player.tv", "playing" if i % 2 else "paused",
             base + timedelta(days=i % 12, hours=(i % 24)))
    conn.commit(); conn.close()

    pa = analyzer.PatternAnalyzer()
    pa._db = str(db)
    diag = pa.pattern_diagnostic()

    # busiest source is the media_player flood
    assert diag["top_sources"] and diag["top_sources"][0]["entity_id"] == "media_player.tv"
    # the light routine shows up as a candidate with its distinct-day count
    porch = [c for c in diag["candidates"]
             if c["entity_id"] == "light.porch" and c["hour"] == 18 and c["state"] == "on"]
    assert porch and porch[0]["days"] == 5
    assert diag["total_days"] >= 12
    assert diag["min_days"] >= 1     # the coverage bar is surfaced


def test_diagnostic_empty_store_is_safe(analyzer, tmp_path):
    db = tmp_path / "e.db"
    _conn(db).close()
    pa = analyzer.PatternAnalyzer()
    pa._db = str(db)
    diag = pa.pattern_diagnostic()
    assert diag["top_sources"] == [] and diag["candidates"] == []
