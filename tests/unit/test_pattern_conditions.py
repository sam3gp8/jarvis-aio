"""Time-window conditions (the "And if") on learned trigger→action sequences.

A sequence that only happens in a clear daily window gets an HA time condition,
so the suggested automation won't fire at the wrong times; a sequence spread
across the day gets none. The condition survives generation → normalize →
install.
"""
import json
from datetime import datetime

import pytest


@pytest.fixture
def pa(load):
    return load("pattern_analyzer")


def _ep(hour, day=1):
    return datetime(2026, 6, day, hour, 0, 0).timestamp()


def test_evening_cluster_yields_window(pa):
    epochs = [_ep(h, d) for d in range(1, 6) for h in (17, 19, 21, 23)]
    assert pa._time_window_condition(epochs) == {"condition": "time", "after": "17:00:00", "before": "00:00:00"}


def test_overnight_cluster_wraps(pa):
    epochs = [_ep(h, d) for d in range(1, 6) for h in (22, 23, 0, 1, 2)]
    assert pa._time_window_condition(epochs) == {"condition": "time", "after": "22:00:00", "before": "03:00:00"}


def test_all_day_spread_has_no_window(pa):
    epochs = [_ep(h, d) for d in range(1, 6) for h in (2, 8, 14, 20)]
    assert pa._time_window_condition(epochs) is None       # gaps all 6h < 8h


def test_single_hour_gets_tight_window(pa):
    epochs = [_ep(18, d) for d in range(1, 7)]
    assert pa._time_window_condition(epochs) == {"condition": "time", "after": "17:00:00", "before": "19:00:00"}


def test_too_few_occurrences_no_window(pa):
    assert pa._time_window_condition([_ep(18), _ep(18)]) is None


def test_generate_automation_includes_condition(pa):
    p = pa.DetectedPattern(
        pattern_type="sequence", description="x",
        entity_ids=["binary_sensor.hall_motion", "light.hall"], confidence=0.7,
        occurrences=8,
        details={"trigger": {"entity": "binary_sensor.hall_motion", "state": "on"},
                 "action": {"entity": "light.hall", "state": "on"},
                 "delay_seconds": 30,
                 "condition": {"condition": "time", "after": "17:00:00", "before": "00:00:00"}})
    auto = json.loads(pa.PatternAnalyzer()._generate_automation(p))
    assert auto["trigger"]["platform"] == "state"
    assert auto["condition"][0]["condition"] == "time"
    assert auto["condition"][0]["after"] == "17:00:00"


def test_no_condition_when_absent(pa):
    p = pa.DetectedPattern(
        pattern_type="sequence", description="x",
        entity_ids=["switch.a", "light.b"], confidence=0.7, occurrences=8,
        details={"trigger": {"entity": "switch.a", "state": "on"},
                 "action": {"entity": "light.b", "state": "on"},
                 "delay_seconds": 30, "condition": None})
    auto = json.loads(pa.PatternAnalyzer()._generate_automation(p))
    assert "condition" not in auto


def test_normalize_preserves_condition(pa):
    stored = json.dumps({
        "alias": "x",
        "trigger": {"platform": "state", "entity_id": "binary_sensor.m", "to": "on"},
        "action": [{"service": "light.turn_on"}],
        "condition": [{"condition": "time", "after": "17:00:00", "before": "00:00:00"}],
    })
    norm = pa.normalize_suggestion_automation(stored)
    assert norm["installable"] is True
    assert norm["condition"][0]["condition"] == "time"


# ── sun-position conditions (the "after dark" And-if) ────────────────────────

def test_sun_condition_when_mostly_dark(pa, monkeypatch):
    monkeypatch.setattr(pa, "_is_dark_at", lambda e, lat, lon: True)
    epochs = [_ep(h, d) for d in range(1, 6) for h in (20, 22)]
    assert pa._sun_condition(epochs, 40.7, -74.0) == {
        "condition": "sun", "after": "sunset", "before": "sunrise"}


def test_no_sun_condition_when_mostly_light(pa, monkeypatch):
    monkeypatch.setattr(pa, "_is_dark_at", lambda e, lat, lon: False)
    epochs = [_ep(h, d) for d in range(1, 6) for h in (10, 12)]
    assert pa._sun_condition(epochs, 40.7, -74.0) is None


def test_sun_condition_mixed_below_threshold(pa, monkeypatch):
    # 3 of 6 dark (50%) is below the 80% bar — not a reliable "after dark" pattern
    seq = iter([True, True, True, False, False, False])
    monkeypatch.setattr(pa, "_is_dark_at", lambda e, lat, lon: next(seq))
    epochs = [_ep(12, d) for d in range(1, 7)]
    assert pa._sun_condition(epochs, 40.7, -74.0) is None


def test_sun_condition_needs_location(pa, monkeypatch):
    monkeypatch.setattr(pa, "_is_dark_at", lambda e, lat, lon: True)
    epochs = [_ep(22, d) for d in range(1, 7)]
    assert pa._sun_condition(epochs, None, None) is None


def test_sun_condition_needs_min_occurrences(pa, monkeypatch):
    monkeypatch.setattr(pa, "_is_dark_at", lambda e, lat, lon: True)
    assert pa._sun_condition([_ep(22), _ep(22)], 40.7, -74.0) is None


def test_generate_automation_emits_sun_condition(pa):
    p = pa.DetectedPattern(
        pattern_type="sequence", description="x",
        entity_ids=["binary_sensor.hall_motion", "light.hall"], confidence=0.7,
        occurrences=9,
        details={"trigger": {"entity": "binary_sensor.hall_motion", "state": "on"},
                 "action": {"entity": "light.hall", "state": "on"},
                 "delay_seconds": 20,
                 "condition": {"condition": "sun", "after": "sunset",
                               "before": "sunrise"}})
    auto = json.loads(pa.PatternAnalyzer()._generate_automation(p))
    assert auto["condition"][0] == {"condition": "sun", "after": "sunset",
                                    "before": "sunrise"}


def test_is_dark_at_with_real_astral(pa):
    pytest.importorskip("astral")
    from datetime import datetime, timezone
    # NYC: 07:00 UTC in mid-January is ~2am EST (before sunrise) → dark
    dark = datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc).timestamp()
    # 17:00 UTC same day is ~noon EST → light
    light = datetime(2026, 1, 15, 17, 0, tzinfo=timezone.utc).timestamp()
    assert pa._is_dark_at(dark, 40.7128, -74.0060) is True
    assert pa._is_dark_at(light, 40.7128, -74.0060) is False
