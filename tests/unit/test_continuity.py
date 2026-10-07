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


# ── Phase I-B: cognitive context capture (intent + chosen plan) ──────────────
# `capture_now` now also attaches the pure CognitiveContext, built from the
# primary active goal (a goal is an outcome pursued across time). Shadow /
# observe-only: it enriches the snapshot and the boot continuity line, drives
# nothing. Only intent + plan are sourced (the rest map to pure/unbuilt
# subsystems and stay empty).

def _patch_goals(load, monkeypatch, rows):
    goals = load("goals")
    monkeypatch.setattr(goals, "active", lambda *a, **k: rows)
    return goals


def test_live_cognitive_from_primary_goal(cont, load, monkeypatch):
    _patch_goals(load, monkeypatch, [
        {"id": 1, "title": "Warm up the house", "outcome": "house is warm by 7am",
         "steps": [{"n": 1, "step": "raise thermostat", "status": "done"},
                   {"n": 2, "step": "close the blinds", "status": "pending"}]},
        {"id": 2, "outcome": "secondary outcome", "steps": []},
    ])
    cog = cont._live_cognitive()
    assert cog is not None
    assert cog.intent == "house is warm by 7am"          # outcome preferred
    assert cog.plan == "1/2 done; next: close the blinds"
    # Only intent + plan are populated; the rest stay empty.
    assert cog.identity == "" and cog.beliefs == ()


def test_live_cognitive_falls_back_to_title(cont, load, monkeypatch):
    _patch_goals(load, monkeypatch, [{"id": 1, "title": "Tidy up", "steps": []}])
    cog = cont._live_cognitive()
    assert cog is not None and cog.intent == "Tidy up" and cog.plan == ""


def test_live_cognitive_none_without_goals(cont, load, monkeypatch):
    _patch_goals(load, monkeypatch, [])
    assert cont._live_cognitive() is None


def test_live_cognitive_defensive_on_reader_failure(cont, load, monkeypatch):
    goals = load("goals")

    def boom(*a, **k):
        raise RuntimeError("goals db down")
    monkeypatch.setattr(goals, "active", boom)
    assert cont._live_cognitive() is None  # swallowed → None, never raises


def test_plan_summary_shapes(cont):
    assert cont._plan_summary({"steps": []}) == ""
    assert cont._plan_summary({}) == ""
    assert cont._plan_summary(
        {"steps": [{"step": "a", "status": "done"},
                   {"step": "b", "status": "completed"}]}) == "2/2 done"
    assert cont._plan_summary(
        {"steps": [{"step": "a", "status": "done"},
                   {"step": "b", "status": "pending"}]}) == "1/2 done; next: b"


def test_capture_attaches_and_persists_cognitive(cont, load, monkeypatch):
    monkeypatch.setattr(cont, "_live_mode", lambda: "home")
    monkeypatch.setattr(cont, "_live_goals",
                        lambda: [{"id": "g1", "label": "warm up", "status": "active"}])
    monkeypatch.setattr(cont, "_live_situations", lambda hass=None: [])
    _patch_goals(load, monkeypatch, [
        {"id": 1, "outcome": "house is warm by 7am",
         "steps": [{"step": "raise thermostat", "status": "pending"}]}])
    state = cont.capture_now()
    assert state is not None and state.cognitive is not None
    assert state.cognitive.intent == "house is warm by 7am"
    assert "raise thermostat" in state.cognitive.plan
    # Survives the persistence round-trip (to_dict/from_dict through the store).
    reloaded = cont._store().load_latest()
    assert reloaded.cognitive is not None
    assert reloaded.cognitive.intent == "house is warm by 7am"
    # ...and it reaches the boot continuity line.
    assert "intent=house is warm by 7am" in cont.boot_summary()


def test_capture_without_goal_stays_commitment_only(cont, load, monkeypatch):
    # No active goal → cognitive dropped → snapshot stays commitment-only
    # (byte-identical to the pre-I-B behaviour).
    monkeypatch.setattr(cont, "_live_mode", lambda: "home")
    monkeypatch.setattr(cont, "_live_goals", lambda: [])
    monkeypatch.setattr(cont, "_live_situations", lambda hass=None: [])
    _patch_goals(load, monkeypatch, [])
    state = cont.capture_now()
    assert state is not None and state.cognitive is None
