"""Conversation-store health probe (database.health) — v7.42.0.

Guards that a schema/connect failure is surfaced (ok=False + error text) rather
than silently swallowed, and that a writable store probes healthy.
"""
from __future__ import annotations


def test_health_ok_on_writable_db(load, tmp_path, monkeypatch):
    db = load("database")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "conversations.db")
    h = db.health()
    assert h["ok"] is True
    assert h["error"] == ""


def test_health_surfaces_error_not_silent(load, tmp_path, monkeypatch):
    db = load("database")
    blocker = tmp_path / "afile"
    blocker.write_text("x")                          # a file where a dir is needed
    monkeypatch.setattr(db, "DB_PATH", blocker / "sub" / "conversations.db")
    h = db.health()
    assert h["ok"] is False
    assert h["error"]                                # captured, not swallowed
