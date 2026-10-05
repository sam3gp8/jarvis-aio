"""Tests for the continuity-of-self live binder (roadmap Phase I, I2 — shadow).

The binder reads live goals/mode/situations and writes an agency snapshot, and
on boot logs the continuity summary. Here the live readers are monkeypatched and
the snapshot DB is redirected to a tmp file, so nothing touches Home Assistant.
"""
import logging

import pytest


@pytest.fixture
def cont(load, tmp_path, monkeypatch):
    mod = load("continuity")
    # Redirect the snapshot DB to a tmp file (the binder resolves it via
    # config_path_str); keep every call pointing at the same path.
    db = str(tmp_path / "agency.db")
    monkeypatch.setattr(mod, "config_path_str", lambda *a, **k: db)
    return mod


def test_capture_persists_a_snapshot_from_live_readers(cont, monkeypatch):
    monkeypatch.setattr(cont, "_live_mode", lambda: "home")
    monkeypatch.setattr(cont, "_live_goals",
                        lambda: [{"id": "g1", "label": "warm up", "status": "active"}])
    monkeypatch.setattr(cont, "_live_situations",
                        lambda hass=None: [{"id": "s1", "label": "delivery", "status": "investigating"}])
    state = cont.capture_now()
    assert state is not None
    assert state.mode == "home"
    assert [c.id for c in state.goals] == ["g1"]
    assert [c.id for c in state.situations] == ["s1"]
    # Persisted: a fresh store on the same path reads it back.
    assert cont._store().load_latest().mode == "home"


def test_boot_summary_logs_prior_snapshot(cont, monkeypatch, caplog):
    monkeypatch.setattr(cont, "_live_mode", lambda: "away")
    monkeypatch.setattr(cont, "_live_goals", lambda: [{"id": "g1"}, {"id": "g2"}])
    monkeypatch.setattr(cont, "_live_situations", lambda hass=None: [])
    cont.capture_now()
    with caplog.at_level(logging.INFO):
        msg = cont.boot_summary()
    assert "mode=away" in msg and "2 goals" in msg
    assert any("continuity" in r.message for r in caplog.records)


def test_boot_summary_no_prior_snapshot(cont):
    assert cont.boot_summary() == "continuity: no prior agency snapshot"


def test_kill_switch_disables_capture_and_boot(cont, monkeypatch):
    monkeypatch.setattr(cont, "AGENCY_CAPTURE_ENABLED", False)
    monkeypatch.setattr(cont, "_live_mode", lambda: "home")
    assert cont.capture_now() is None
    assert cont.boot_summary() == ""
    # Nothing was written.
    assert cont._store().load_latest() is None


def test_boot_reconcile_splits_live_from_vanished(cont, monkeypatch, caplog):
    # Snapshot a world with two goals + one situation...
    monkeypatch.setattr(cont, "_live_mode", lambda: "home")
    monkeypatch.setattr(cont, "_live_goals",
                        lambda: [{"id": "g1"}, {"id": "g2"}])
    monkeypatch.setattr(cont, "_live_situations",
                        lambda hass=None: [{"id": "s1", "status": "open"}])
    cont.capture_now()
    # ...then a restart where only g1 and s1 are still live (g2 vanished).
    monkeypatch.setattr(cont, "_live_goals", lambda: [{"id": "g1"}])
    monkeypatch.setattr(cont, "_live_situations",
                        lambda hass=None: [{"id": "s1", "status": "open"}])
    with caplog.at_level(logging.INFO):
        rep = cont.boot_reconcile()
    assert {c.id for c in rep.still_live} == {"g1", "s1"}
    assert {c.id for c in rep.vanished} == {"g2"}
    assert any("continuity reconcile" in r.message for r in caplog.records)


def test_boot_reconcile_none_without_prior_snapshot(cont):
    assert cont.boot_reconcile() is None


def test_boot_reconcile_kill_switch(cont, monkeypatch):
    monkeypatch.setattr(cont, "_live_mode", lambda: "home")
    monkeypatch.setattr(cont, "_live_goals", lambda: [{"id": "g1"}])
    monkeypatch.setattr(cont, "_live_situations", lambda hass=None: [])
    cont.capture_now()
    monkeypatch.setattr(cont, "AGENCY_CAPTURE_ENABLED", False)
    assert cont.boot_reconcile() is None


def test_capture_swallows_a_failing_reader(cont, monkeypatch):
    # A reader that blows up must never propagate out of capture_now.
    def boom(*a, **k):
        raise RuntimeError("backend down")
    monkeypatch.setattr(cont, "_live_mode", boom)
    monkeypatch.setattr(cont, "_live_goals", lambda: [])
    monkeypatch.setattr(cont, "_live_situations", lambda hass=None: [])
    assert cont.capture_now() is None  # caught, returns None, no raise
