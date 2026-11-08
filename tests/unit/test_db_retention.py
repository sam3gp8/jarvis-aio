"""DB retention / pruning to bound uncontrolled growth (issue #491).

StateLogger.prune_old caps patterns.db (state_changes/commands) to a window;
database.purge_old_records caps jarvis.db. Both treat days<=0 as "keep forever".
"""
from datetime import datetime, timedelta
import sqlite3

import pytest


@pytest.fixture
def cc(load):
    return load("cognitive_core")


def _count(db, tbl):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    finally:
        conn.close()


def test_prune_old_deletes_old_keeps_recent(cc, tmp_path):
    db = str(tmp_path / "p.db")
    sl = cc.StateLogger(db_path=db)
    old = (datetime.now() - timedelta(days=100)).isoformat()
    recent = (datetime.now() - timedelta(days=1)).isoformat()
    sl.bulk_insert_history([
        (old, "light.hall", "light", "off", "on"),
        (old, "light.hall", "light", "on", "off"),
        (recent, "light.hall", "light", "off", "on"),
    ])
    assert _count(db, "state_changes") == 3
    deleted = sl.prune_old(30)
    assert deleted == 2                               # the two 100-day-old rows
    assert _count(db, "state_changes") == 1           # the recent row survives


def test_prune_old_zero_or_negative_keeps_everything(cc, tmp_path):
    db = str(tmp_path / "p.db")
    sl = cc.StateLogger(db_path=db)
    old = (datetime.now() - timedelta(days=500)).isoformat()
    sl.bulk_insert_history([(old, "light.x", "light", "off", "on")])
    assert sl.prune_old(0) == 0                        # retention disabled
    assert sl.prune_old(-5) == 0                       # negative also disabled
    assert _count(db, "state_changes") == 1           # nothing removed


def test_prune_old_bad_input_is_safe(cc, tmp_path):
    db = str(tmp_path / "p.db")
    sl = cc.StateLogger(db_path=db)
    assert sl.prune_old("not-a-number") == 0          # coerces → 0, never raises


def test_purge_old_records_zero_is_noop(load):
    database = load("database")
    # days<=0 returns before touching the DB → safe to call with no real store.
    assert database.purge_old_records(0) == 0
    assert database.purge_old_records(-1) == 0
