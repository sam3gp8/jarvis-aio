"""History backfill for Analyze Now — safety-critical pieces.

The anti-flood core (_backfill_filter_states) must keep a motion-storm history
bounded, and the store methods must round-trip. This is what lets backfill import
past behavior without reintroducing the flood or double-counting.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


def test_motion_storm_history_is_bounded(cc):
    # 30 days of a motion sensor pulsing every 3s, imported through the filter
    events = [(1000.0 + i * 3.0, "on" if i % 2 else "off") for i in range(20000)]
    kept = cc._backfill_filter_states(events, interval=300.0, cap=1000)
    assert len(kept) <= 1000, f"backfill not capped: {len(kept)}"
    # spacing respects the rate limit
    for a, b in zip(kept, kept[1:]):
        assert b[0] - a[0] >= 300.0


def test_filter_drops_unavailable_and_nochange(cc):
    events = [
        (100.0, "on"), (101.0, "on"),          # no-change
        (500.0, "unavailable"),                 # dropped
        (900.0, "off"), (1300.0, "on"),
    ]
    kept = cc._backfill_filter_states(events, interval=300.0, cap=1000)
    states = [s for _, s in kept]
    assert "unavailable" not in states
    assert states == ["on", "off", "on"]       # first on, then off, then on


def test_sparse_events_all_kept(cc):
    # occupancy-style: a few events hours apart — none dropped by the rate limit
    events = [(i * 3600.0, "on" if i % 2 == 0 else "off") for i in range(10)]
    kept = cc._backfill_filter_states(events, interval=300.0, cap=1000)
    assert len(kept) == 10


def test_bulk_insert_and_distinct(cc, tmp_path, monkeypatch):
    db = str(tmp_path / "p.db")
    # db_path injected → StateLogger builds its real schema here, never /config
    sl = cc.StateLogger(db_path=db)
    t = datetime(2026, 6, 1, 18, 0, 0)
    n = sl.bulk_insert_history([
        (t.isoformat(), "binary_sensor.bay_occupancy", "binary_sensor", "unknown", "on"),
        ((t + timedelta(minutes=1)).isoformat(), "cover.garage", "cover", "unknown", "closed"),
    ])
    assert n == 2
    got = sl.distinct_logged_entities()
    assert got == {"binary_sensor.bay_occupancy", "cover.garage"}
    # hour/day_of_week derive from the historical timestamp, not now
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT hour, triggered_by FROM state_changes "
                       "WHERE entity_id='binary_sensor.bay_occupancy'").fetchone()
    conn.close()
    assert row[0] == 18 and row[1] == "history"
