"""database.get_recent_messages returns the MOST RECENT `limit` rows, oldest first.

Regression: the query was `ORDER BY timestamp ASC LIMIT ?`, so once the window
held more than `limit` rows it returned the OLDEST ones — conversation context
fed to the agent was the stale start of the window, not the latest turns. The
fix takes the newest `limit` rows (DESC) and re-sorts them ascending. Both the
device-filtered and unfiltered branches are covered, since each has its own SQL.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def db(load, tmp_path, monkeypatch):
    mod = load("database")
    monkeypatch.setattr(mod, "DB_PATH", tmp_path / "conversations.db")
    return mod


def _seed(db, rows):
    """Insert (minutes_ago, device_id, content) rows with explicit timestamps so
    ordering is deterministic (save_message stamps now(), which can collide)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with db._connect() as conn:
        conn.executemany(
            "INSERT INTO conversations (timestamp, device_id, role, content) "
            "VALUES (?,?,?,?)",
            [((now - timedelta(minutes=m)).isoformat(), dev, "user", text)
             for m, dev, text in rows],
        )


def _contents(rows):
    return [r["content"] for r in rows]


def test_limit_keeps_newest_rows_oldest_first(db):
    # m5 is oldest … m1 newest; inserted out of order to rule out id-order luck.
    _seed(db, [(3, "kitchen", "m3"), (5, "kitchen", "m5"), (1, "kitchen", "m1"),
               (4, "kitchen", "m4"), (2, "kitchen", "m2")])
    assert _contents(db.get_recent_messages(limit=3)) == ["m3", "m2", "m1"]


def test_limit_keeps_newest_rows_for_device(db):
    _seed(db, [(5, "kitchen", "k5"), (4, "office", "o4"), (3, "kitchen", "k3"),
               (2, "office", "o2"), (1, "kitchen", "k1")])
    assert _contents(db.get_recent_messages(device_id="kitchen", limit=2)) == ["k3", "k1"]


def test_under_limit_returns_all_in_window_oldest_first(db):
    _seed(db, [(2, "kitchen", "a"), (1, "kitchen", "b"),
               (60 * 48, "kitchen", "outside-window")])
    assert _contents(db.get_recent_messages(hours=24, limit=10)) == ["a", "b"]
