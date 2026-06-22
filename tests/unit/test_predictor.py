"""Regression tests for PredictiveHabitMatrix.

Stdlib-only; tested against tmp_path. Pins the recurrence-probability moving
average, the 90% pre-empt threshold, time-bucketing, due-preemption lookahead,
persistence, the rolling cap, and corrupt-file tolerance.
"""
import datetime as dt
import importlib.util
import json
import pathlib
import sys

import pytest

COMP = pathlib.Path(__file__).resolve().parents[2] / "jarvis_assistant" / "jarvis_component"


def _load_standalone(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, COMP / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


phm = _load_standalone("jarvis_predictor", "automation/predictor.py")


def ts_at(year, month, day, hour, minute):
    """A local-time timestamp; the predictor buckets with the same local tz."""
    return dt.datetime(year, month, day, hour, minute).timestamp()


@pytest.fixture
def matrix(tmp_path):
    return phm.PredictiveHabitMatrix(path=str(tmp_path / "habit.json"))


def test_full_recurrence_is_certain(matrix):
    for d in range(1, 11):  # 10 distinct days, all with the event in the 08:00 slot
        matrix.record_event("office_entry", ts_at(2025, 1, d, 8, 15))
    p = matrix.probability("office_entry", ts_at(2025, 1, 11, 8, 20))  # same 30-min bucket
    assert p == 1.0
    assert matrix.should_preempt("office_entry", ts_at(2025, 1, 11, 8, 20)) is True


def test_partial_recurrence_below_threshold(matrix):
    for d in range(1, 11):
        if d <= 8:
            matrix.record_event("office_entry", ts_at(2025, 1, d, 8, 15))
        else:
            # A different event keeps the day "observed" without the office entry.
            matrix.record_event("kitchen_entry", ts_at(2025, 1, d, 8, 15))
    p = matrix.probability("office_entry", ts_at(2025, 1, 11, 8, 15))
    assert p == 0.8
    assert matrix.should_preempt("office_entry", ts_at(2025, 1, 11, 8, 15)) is False


def test_different_slot_is_zero(matrix):
    for d in range(1, 11):
        matrix.record_event("office_entry", ts_at(2025, 1, d, 8, 15))
    assert matrix.probability("office_entry", ts_at(2025, 1, 11, 14, 0)) == 0.0


def test_no_history_is_zero(matrix):
    assert matrix.probability("anything", ts_at(2025, 1, 1, 9, 0)) == 0.0


def test_due_preemptions_lookahead(matrix):
    for d in range(1, 11):
        matrix.record_event("office_hvac_on", ts_at(2025, 1, d, 8, 15))
    # now + 7 min lands at 08:17 → the 08:00–08:30 bucket where the habit lives
    now = ts_at(2025, 1, 11, 8, 10)
    due = matrix.due_preemptions(now, lead_minutes=7)
    assert any(item["key"] == "office_hvac_on" and item["probability"] == 1.0 for item in due)


def test_due_preemptions_empty_when_below_threshold(matrix):
    for d in range(1, 11):
        if d <= 5:
            matrix.record_event("office_hvac_on", ts_at(2025, 1, d, 8, 15))
        else:
            matrix.record_event("noise", ts_at(2025, 1, d, 8, 15))
    now = ts_at(2025, 1, 11, 8, 10)
    assert matrix.due_preemptions(now, lead_minutes=7) == []


def test_persistence_across_instances(tmp_path):
    path = str(tmp_path / "habit.json")
    a = phm.PredictiveHabitMatrix(path=path)
    a.record_event("x", ts_at(2025, 1, 1, 8, 0))
    b = phm.PredictiveHabitMatrix(path=path)
    assert b.observed_days() == 1


def test_rolling_cap(tmp_path):
    path = tmp_path / "habit.json"
    m = phm.PredictiveHabitMatrix(path=str(path), max_events=5)
    for i in range(20):
        m.record_event("e", ts_at(2025, 1, 1, 8, i % 60))
    data = json.loads(path.read_text())
    assert len(data) == 5


def test_corrupt_file_tolerated(tmp_path):
    path = tmp_path / "habit.json"
    path.write_text("{ not valid json")
    m = phm.PredictiveHabitMatrix(path=str(path))
    assert m.observed_days() == 0          # doesn't raise
    m.record_event("e", ts_at(2025, 1, 1, 8, 0))
    assert m.observed_days() == 1


def test_custom_bucket_minutes(tmp_path):
    # With 60-minute buckets, 08:15 and 08:45 share a slot.
    m = phm.PredictiveHabitMatrix(path=str(tmp_path / "h.json"), bucket_minutes=60)
    for d in range(1, 11):
        m.record_event("office_entry", ts_at(2025, 1, d, 8, 15))
    assert m.probability("office_entry", ts_at(2025, 1, 11, 8, 45)) == 1.0
