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
    # Redirect each DB the binder resolves via config_path_str to its own tmp
    # file, keyed by the filename arg (config_path_str("jarvis", "<name>.db")),
    # exactly as production keeps agency.db / journal.db / situations.db apart —
    # each kernel.persistence DB has its own schema-version row, so they must not
    # collide on one file.
    def _path(*a, **k):
        name = a[1] if len(a) > 1 else "agency.db"
        return str(tmp_path / name)
    monkeypatch.setattr(mod, "config_path_str", _path)
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
    # No active goal and no hass (so no belief view) → cognitive dropped →
    # snapshot stays commitment-only (byte-identical to the pre-I-B behaviour).
    monkeypatch.setattr(cont, "_live_mode", lambda: "home")
    monkeypatch.setattr(cont, "_live_goals", lambda: [])
    monkeypatch.setattr(cont, "_live_situations", lambda hass=None: [])
    _patch_goals(load, monkeypatch, [])
    state = cont.capture_now()
    assert state is not None and state.cognitive is None


# ── Phase I-B (enrichment): salient beliefs from the WorldModel belief view ──

class _FakeBelief:
    def __init__(self, proposition, probability):
        self.proposition = proposition
        self.probability = probability


def _patch_worldmodel(load, monkeypatch, beliefs):
    wm_mod = load("kernel.world_model")

    class _FakeWM:
        def __init__(self, hass, config=None):
            pass

        def beliefs(self, subject=None):
            return list(beliefs)

    monkeypatch.setattr(wm_mod, "WorldModel", _FakeWM)
    return wm_mod


def test_live_beliefs_drops_identity_ranks_and_renders(cont, load, monkeypatch):
    _patch_worldmodel(load, monkeypatch, [
        _FakeBelief("I am JARVIS", 0.99),              # leading identity → dropped
        _FakeBelief("kitchen.temperature=21", 0.60),
        _FakeBelief("front_door.locked=true", 0.95),
        _FakeBelief("garage.open=false", 0.80),
    ])
    out = cont._live_beliefs(hass=object())
    # Identity dropped; ranked by confidence desc; each rendered with p=.
    assert out[0] == "front_door.locked=true (p=0.95)"
    assert out == (
        "front_door.locked=true (p=0.95)",
        "garage.open=false (p=0.80)",
        "kitchen.temperature=21 (p=0.60)",
    )
    assert all("JARVIS" not in b for b in out)


def test_live_beliefs_caps_count(cont, load, monkeypatch):
    many = [_FakeBelief("identity", 0.99)] + [
        _FakeBelief(f"f{i}=x", 0.9 - i * 0.01) for i in range(20)]
    _patch_worldmodel(load, monkeypatch, many)
    assert len(cont._live_beliefs(hass=object())) == cont._MAX_BELIEFS


def test_live_beliefs_empty_without_hass(cont):
    assert cont._live_beliefs(None) == ()


def test_live_beliefs_defensive_on_failure(cont, load, monkeypatch):
    wm_mod = load("kernel.world_model")

    class _BoomWM:
        def __init__(self, hass, config=None):
            pass

        def beliefs(self, subject=None):
            raise RuntimeError("knowledge store down")

    monkeypatch.setattr(wm_mod, "WorldModel", _BoomWM)
    assert cont._live_beliefs(hass=object()) == ()


def test_capture_attaches_beliefs_even_without_a_goal(cont, load, monkeypatch):
    # With a live hass and salient beliefs but no active goal, the cognitive
    # context is still captured (beliefs alone), and persists.
    monkeypatch.setattr(cont, "_live_mode", lambda: "home")
    monkeypatch.setattr(cont, "_live_goals", lambda: [])
    monkeypatch.setattr(cont, "_live_situations", lambda hass=None: [])
    _patch_goals(load, monkeypatch, [])
    _patch_worldmodel(load, monkeypatch, [
        _FakeBelief("I am JARVIS", 0.99),
        _FakeBelief("front_door.locked=true", 0.95),
    ])
    state = cont.capture_now(hass=object())
    assert state is not None and state.cognitive is not None
    assert state.cognitive.intent == ""     # no active goal
    assert state.cognitive.beliefs == ("front_door.locked=true (p=0.95)",)
    # survives the store round-trip
    assert cont._store().load_latest().cognitive.beliefs == (
        "front_door.locked=true (p=0.95)",)


# ── Phase I-B (enrichment): in-flight execution from the kernel journal ──────

class _FakeStep:
    def __init__(self, action, status="running"):
        self.action = action
        self.status = status


def _patch_journal(load, monkeypatch, in_flight):
    jr_mod = load("kernel.journal")

    class _FakeJournal:
        def __init__(self, db_path, **k):
            pass

        def in_flight(self):
            return list(in_flight)

    monkeypatch.setattr(jr_mod, "ExecutionJournal", _FakeJournal)
    return jr_mod


def test_live_execution_single_step(cont, load, monkeypatch):
    _patch_journal(load, monkeypatch, [_FakeStep("lock front_door")])
    assert cont._live_execution() == "running: lock front_door"


def test_live_execution_multiple_steps(cont, load, monkeypatch):
    _patch_journal(load, monkeypatch, [
        _FakeStep("lock front_door"), _FakeStep("arm alarm"), _FakeStep("close garage")])
    assert cont._live_execution() == "3 steps running; e.g. lock front_door"


def test_live_execution_empty_when_nothing_in_flight(cont, load, monkeypatch):
    _patch_journal(load, monkeypatch, [])
    assert cont._live_execution() == ""


def test_live_execution_defensive_on_failure(cont, load, monkeypatch):
    jr_mod = load("kernel.journal")

    class _BoomJournal:
        def __init__(self, db_path, **k):
            raise RuntimeError("journal db down")

    monkeypatch.setattr(jr_mod, "ExecutionJournal", _BoomJournal)
    assert cont._live_execution() == ""


def test_capture_attaches_execution(cont, load, monkeypatch):
    # No goal, no beliefs, but a step mid-flight → cognitive captures execution.
    monkeypatch.setattr(cont, "_live_mode", lambda: "home")
    monkeypatch.setattr(cont, "_live_goals", lambda: [])
    monkeypatch.setattr(cont, "_live_situations", lambda hass=None: [])
    _patch_goals(load, monkeypatch, [])
    _patch_worldmodel(load, monkeypatch, [])
    _patch_journal(load, monkeypatch, [_FakeStep("lock front_door")])
    state = cont.capture_now()
    assert state is not None and state.cognitive is not None
    assert state.cognitive.execution == "running: lock front_door"
    assert state.cognitive.intent == "" and state.cognitive.beliefs == ()
    # reaches the boot continuity line and persists
    assert "running: lock front_door" not in cont.boot_summary()  # summary omits execution
    assert cont._store().load_latest().cognitive.execution == "running: lock front_door"
