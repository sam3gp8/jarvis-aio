"""Intrusion outcome attribution — v7.42.0.

A called-off intrusion must attach its "wrong" verdict to the EXACT decision
record for that intrusion (via set_outcome by id), not to the most-recent-of-
kind guess (set_outcome_recent), which mis-attributes when intrusions overlap.

The `load` fixture shares module instances, so each test resets the intrusion
module globals it touches in a finally.
"""
from __future__ import annotations

import sys
import types


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
