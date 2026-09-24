"""Intrusion outcome attribution — v7.42.0.

A called-off intrusion must attach its "wrong" verdict to the EXACT decision
record for that intrusion (via set_outcome by id), not to the most-recent-of-
kind guess (set_outcome_recent), which mis-attributes when intrusions overlap.

The `load` fixture shares module instances, so each test resets the intrusion
module globals it touches in a finally.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest


def _install_dr(monkeypatch, outcome_ok=True):
    calls = {"set_outcome": [], "set_outcome_recent": []}
    fake = types.ModuleType("jc.decision_record")

    def set_outcome(record_id, verdict, source="", ts=None, db_path=None):
        calls["set_outcome"].append((record_id, verdict, source))
        return outcome_ok

    def set_outcome_recent(kind, verdict, source="", max_age=3600.0, ts=None, db_path=None):
        calls["set_outcome_recent"].append((kind, verdict, source))
        return True

    fake.set_outcome = set_outcome
    fake.set_outcome_recent = set_outcome_recent
    monkeypatch.setitem(sys.modules, "jc.decision_record", fake)
    monkeypatch.setattr(sys.modules["jc"], "decision_record", fake, raising=False)
    return calls


def _reset(intr):
    intr.clear_calloff()
    intr._last_decision_id = None
    intr._pending_decision_generations.clear()
    intr._dismissed_decision_generations.clear()
    try:
        intr._false_alarms.clear()
    except Exception:
        pass


def test_dismiss_attaches_outcome_to_exact_record(load, monkeypatch):
    intr = load("intrusion")
    calls = _install_dr(monkeypatch)
    intr.set_last_decision_id(4242)
    try:
        intr.dismiss_intrusion("false alarm")
        assert calls["set_outcome"] == [(4242, "wrong", "dismiss_intrusion")]
        assert calls["set_outcome_recent"] == []      # exact id used, not the guess
    finally:
        _reset(intr)


def test_dismiss_falls_back_to_recent_without_id(load, monkeypatch):
    intr = load("intrusion")
    intr._last_decision_id = None
    calls = _install_dr(monkeypatch)
    try:
        intr.dismiss_intrusion("false alarm")
        assert calls["set_outcome"] == []
        assert calls["set_outcome_recent"]
        assert calls["set_outcome_recent"][0][:2] == ("intrusion", "wrong")
    finally:
        _reset(intr)


def test_dismiss_falls_back_when_record_already_judged(load, monkeypatch):
    intr = load("intrusion")
    calls = _install_dr(monkeypatch, outcome_ok=False)   # record gone / already judged
    intr.set_last_decision_id(99)
    try:
        intr.dismiss_intrusion("x")
        assert calls["set_outcome"] == [(99, "wrong", "dismiss_intrusion")]
        assert calls["set_outcome_recent"][0][:2] == ("intrusion", "wrong")
    finally:
        _reset(intr)


def test_set_last_decision_id_ignores_none(load):
    intr = load("intrusion")
    intr._last_decision_id = None
    try:
        intr.set_last_decision_id(None)
        assert intr._last_decision_id is None
        intr.set_last_decision_id(7)
        assert intr._last_decision_id == 7
    finally:
        _reset(intr)


def test_set_last_decision_id_ignores_stale_generation(load):
    intr = load("intrusion")
    intr._last_decision_id = None
    generation = intr.begin_decision_generation()
    intr._last_decision_id = 42
    try:
        intr._dismiss_intrusion_state("faa")
        assert intr.set_last_decision_id(99, generation=generation) == 99
        assert intr._last_decision_id is None
    finally:
        _reset(intr)


def test_pending_generation_dismissal_does_not_use_previous_record(load, monkeypatch):
    intr = load("intrusion")
    calls = _install_dr(monkeypatch)
    intr._last_decision_id = 42
    generation = intr.begin_decision_generation()
    try:
        result, decision_id, decision_pending = intr._dismiss_intrusion_state("faa")

        assert result["ok"] is True
        assert decision_id is None
        assert decision_pending is True
        assert calls["set_outcome"] == []
        assert calls["set_outcome_recent"] == []

        dismissed_id = intr.set_last_decision_id(99, generation=generation)
        intr._persist_dismissal(dismissed_id)

        assert calls["set_outcome"] == [(99, "wrong", "dismiss_intrusion")]
        assert calls["set_outcome_recent"] == []
    finally:
        _reset(intr)


@pytest.mark.asyncio
async def test_async_dismissal_shields_executor_future_through_cancellation(
        load, monkeypatch):
    intr = load("intrusion")
    calls = _install_dr(monkeypatch)
    intr.set_last_decision_id(77)
    release = asyncio.Event()

    class FakeHass:
        def async_add_executor_job(self, func, *args):
            future = asyncio.get_running_loop().create_future()

            async def run_job():
                await release.wait()
                func(*args)
                future.set_result(None)

            asyncio.create_task(run_job())
            return future

    try:
        task = asyncio.create_task(
            intr.async_dismiss_intrusion(FakeHass(), "false alarm")
        )
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert calls["set_outcome"] == [(77, "wrong", "dismiss_intrusion")]
    finally:
        _reset(intr)


@pytest.mark.asyncio
async def test_async_record_acceptance_persists_after_cancellation(load):
    manager = load("cognitive_core").AutonomyManager()
    calls = []

    async def fake_save(_hass):
        calls.append("save")
        await asyncio.sleep(0)

    manager._async_save = fake_save
    task = asyncio.create_task(manager.async_record_acceptance(object(), "pattern_x"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls == ["save"]
    assert manager._grants["pattern_x"]["approvals"] == 1
