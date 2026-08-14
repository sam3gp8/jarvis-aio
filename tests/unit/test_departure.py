"""Tests for leave-time / departure anticipation (v6.84.0).

predict_departure warns once, when it's time to head out for the nearest
upcoming timed calendar event, using a travel-time sensor (minutes) plus a
buffer when configured, else a default lead. Alerts flow through the same gated
announce path as the other predictors.
"""
import datetime
import time

import pytest


@pytest.fixture
def cog(load):
    c = load("cognition")
    c._RECUR_ALERTED.clear()
    return c


@pytest.fixture
def cal(cog, load, monkeypatch):
    """Controllable calendar + config; returns (events_holder, cfg)."""
    comms = load("comms")
    jc = load("jarvis_config")
    holder = {"list": []}
    monkeypatch.setattr(comms, "gather_events", lambda hass: holder["list"])
    cfg = {"departure_alerts_enabled": True, "departure_lead_minutes": 30,
           "departure_travel_sensor": ""}
    monkeypatch.setattr(jc, "get", lambda k, d=None: cfg.get(k, d))
    return holder, cfg


def _ev(start_dt, title="Dentist", all_day=False, location=None):
    return {"calendar": "calendar.x", "title": title, "start": start_dt,
            "end": start_dt + datetime.timedelta(hours=1),
            "all_day": all_day, "location": location, "active": False}


def _now():
    now = time.time()
    return now, datetime.datetime.fromtimestamp(now)


def test_alerts_when_time_to_leave(cog, cal, fake_hass):
    holder, cfg = cal
    now, now_dt = _now()
    # event in 20 min, lead 30 → leave time was 10 min ago → alert now
    holder["list"] = [_ev(now_dt + datetime.timedelta(minutes=20), location="Main St")]
    preds = cog.predict_departure(fake_hass, now)
    assert len(preds) == 1
    p = preds[0]
    assert p["type"] == "anticipation_departure" and p["urgency"] == "low"
    assert "Dentist" in p["message"] and "Main St" in p["message"]


def test_no_alert_before_leave_time(cog, cal, fake_hass):
    holder, cfg = cal
    now, now_dt = _now()
    # event in 90 min, lead 30 → leave time is 60 min out → not yet
    holder["list"] = [_ev(now_dt + datetime.timedelta(minutes=90))]
    assert cog.predict_departure(fake_hass, now) == []


def test_dedup_same_day(cog, cal, fake_hass):
    holder, cfg = cal
    now, now_dt = _now()
    holder["list"] = [_ev(now_dt + datetime.timedelta(minutes=20))]
    assert len(cog.predict_departure(fake_hass, now)) == 1
    assert cog.predict_departure(fake_hass, now) == []      # no repeat same day


def test_disabled(cog, cal, fake_hass):
    holder, cfg = cal
    cfg["departure_alerts_enabled"] = False
    now, now_dt = _now()
    holder["list"] = [_ev(now_dt + datetime.timedelta(minutes=20))]
    assert cog.predict_departure(fake_hass, now) == []


def test_all_day_skipped(cog, cal, fake_hass):
    holder, cfg = cal
    now, now_dt = _now()
    holder["list"] = [_ev(now_dt + datetime.timedelta(minutes=20), all_day=True)]
    assert cog.predict_departure(fake_hass, now) == []


def test_beyond_horizon_skipped(cog, cal, fake_hass):
    holder, cfg = cal
    now, now_dt = _now()
    holder["list"] = [_ev(now_dt + datetime.timedelta(hours=5))]   # beyond 3h lookahead
    assert cog.predict_departure(fake_hass, now) == []


def test_no_events(cog, cal, fake_hass):
    assert cog.predict_departure(fake_hass, time.time()) == []


def test_travel_sensor_extends_lead(cog, cal, fake_hass, monkeypatch):
    holder, cfg = cal
    now, now_dt = _now()
    # event in 40 min. Default lead 30 → leave 10 min out → NO alert.
    holder["list"] = [_ev(now_dt + datetime.timedelta(minutes=40))]
    assert cog.predict_departure(fake_hass, now) == []          # default: not yet

    cog._RECUR_ALERTED.clear()
    cfg["departure_travel_sensor"] = "sensor.commute"

    class _St:
        state = "45"                                            # 45 min travel

    monkeypatch.setattr(fake_hass.states, "get",
                        lambda eid: _St() if eid == "sensor.commute" else None)
    # travel lead = 45 + 5 buffer = 50 → leave time was 10 min ago → alert
    preds = cog.predict_departure(fake_hass, now)
    assert len(preds) == 1
