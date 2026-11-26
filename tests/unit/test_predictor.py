"""Regression tests for PredictiveHabitMatrix.

Stdlib-only; tested against tmp_path. Pins the recurrence-probability moving
average, the 90% pre-empt threshold, time-bucketing, due-preemption lookahead,
persistence, the rolling cap, corrupt-file tolerance, the minimum observed-days
floor, weekday/weekend separation, and the single-read batch path (#40, §5).
"""
import datetime as dt
import importlib.util
import json
import pathlib
import sys

import pytest

COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"


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


def _weekdays(count, *, start=dt.date(2025, 1, 6)):  # 2025-01-06 is a Monday
    """The first ``count`` weekday (Mon–Fri) dates on/after ``start``. The matrix
    now scopes probability to the weekday/weekend class, so recurrence tests use a
    single class with enough distinct days to clear the DEFAULT_MIN_DAYS floor."""
    out, d = [], start
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def ts_on(day: dt.date, hour, minute):
    return dt.datetime(day.year, day.month, day.day, hour, minute).timestamp()


@pytest.fixture
def matrix(tmp_path):
    return phm.PredictiveHabitMatrix(path=str(tmp_path / "habit.json"))


def test_full_recurrence_is_certain(matrix):
    days = _weekdays(8)
    for d in days[:7]:  # 7 distinct weekdays, all with the event in the 08:00 slot
        matrix.record_event("office_entry", ts_on(d, 8, 15))
    q = days[7]
    p = matrix.probability("office_entry", ts_on(q, 8, 20))  # same 30-min bucket
    assert p == 1.0
    assert matrix.should_preempt("office_entry", ts_on(q, 8, 20)) is True


def test_partial_recurrence_below_threshold(matrix):
    days = _weekdays(11)
    for i, d in enumerate(days[:10]):
        if i < 8:
            matrix.record_event("office_entry", ts_on(d, 8, 15))
        else:
            # A different event keeps the day "observed" without the office entry.
            matrix.record_event("kitchen_entry", ts_on(d, 8, 15))
    q = days[10]
    p = matrix.probability("office_entry", ts_on(q, 8, 15))
    assert p == 0.8
    assert matrix.should_preempt("office_entry", ts_on(q, 8, 15)) is False


def test_different_slot_is_zero(matrix):
    days = _weekdays(8)
    for d in days[:7]:
        matrix.record_event("office_entry", ts_on(d, 8, 15))
    assert matrix.probability("office_entry", ts_on(days[7], 14, 0)) == 0.0


def test_no_history_is_zero(matrix):
    assert matrix.probability("anything", ts_at(2025, 1, 1, 9, 0)) == 0.0


def test_due_preemptions_lookahead(matrix):
    days = _weekdays(8)
    for d in days[:7]:
        matrix.record_event("office_hvac_on", ts_on(d, 8, 15))
    # now + 7 min lands at 08:17 → the 08:00–08:30 bucket where the habit lives
    now = ts_on(days[7], 8, 10)
    due = matrix.due_preemptions(now, lead_minutes=7)
    assert any(item["key"] == "office_hvac_on" and item["probability"] == 1.0 for item in due)


def test_due_preemptions_empty_when_below_threshold(matrix):
    days = _weekdays(11)
    for i, d in enumerate(days[:10]):
        if i < 5:
            matrix.record_event("office_hvac_on", ts_on(d, 8, 15))
        else:
            matrix.record_event("noise", ts_on(d, 8, 15))
    now = ts_on(days[10], 8, 10)
    assert matrix.due_preemptions(now, lead_minutes=7) == []


# ── minimum observed-days floor (#40, §5.2) ──────────────────────────────────

def test_below_min_days_is_not_trusted(matrix):
    # 3 weekdays, all with the event — but < DEFAULT_MIN_DAYS (7), so a Day-1
    # style habit can't score 1.0 and pre-empt on no real evidence.
    days = _weekdays(4)
    for d in days[:3]:
        matrix.record_event("office_entry", ts_on(d, 8, 15))
    assert matrix.probability("office_entry", ts_on(days[3], 8, 20)) == 0.0
    assert matrix.should_preempt("office_entry", ts_on(days[3], 8, 20)) is False
    # an explicit low min_days opts into the small-sample estimate
    assert matrix.probability("office_entry", ts_on(days[3], 8, 20), min_days=1) == 1.0


# ── weekday / weekend separation (#40, §5.3) ─────────────────────────────────

def test_weekday_habit_does_not_predict_weekend(matrix):
    for d in _weekdays(7):
        matrix.record_event("gym", ts_on(d, 18, 0))
    sat = dt.date(2025, 1, 11)              # a Saturday
    assert sat.weekday() >= 5
    # even with min_days=1, there is zero weekend history for this habit
    assert matrix.probability("gym", ts_on(sat, 18, 10), min_days=1) == 0.0


def test_weekend_flag_persisted_on_record(matrix):
    ev = matrix.record_event("x", ts_on(dt.date(2025, 1, 11), 9, 0))  # Saturday
    assert ev["weekend"] is True
    ev2 = matrix.record_event("x", ts_on(dt.date(2025, 1, 6), 9, 0))  # Monday
    assert ev2["weekend"] is False


# ── N+1 elimination (#40, §5.1) ──────────────────────────────────────────────

def test_due_preemptions_reads_matrix_once(matrix, monkeypatch):
    for d in _weekdays(7):
        matrix.record_event("a", ts_on(d, 8, 15))
        matrix.record_event("b", ts_on(d, 8, 15))
    calls = {"n": 0}
    real_load = matrix._load

    def _counting_load():
        calls["n"] += 1
        return real_load()

    monkeypatch.setattr(matrix, "_load", _counting_load)
    matrix.due_preemptions(ts_on(_weekdays(8)[7], 8, 10), lead_minutes=7)
    assert calls["n"] == 1          # one load for all keys, not one per key


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
    days = _weekdays(8)
    for d in days[:7]:
        m.record_event("office_entry", ts_on(d, 8, 15))
    assert m.probability("office_entry", ts_on(days[7], 8, 45)) == 1.0
