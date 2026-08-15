"""Tests for leave-time / departure anticipation (v6.84.0, reworked v6.89.0).

predict_departure is async and computes travel time from device-tracking origin
to the event's geocoded location via open-source routing (travel.py), with an
explicit sensor as an optional override and a fixed default lead as the fallback.
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
    cfg = {"departure_alerts_enabled": True, "departure_lead_minutes": 30}
    monkeypatch.setattr(jc, "get", lambda k, d=None: cfg.get(k, d))
    return holder, cfg


def _ev(start_dt, title="Dentist", all_day=False, location=None):
    return {"calendar": "calendar.x", "title": title, "start": start_dt,
            "end": start_dt + datetime.timedelta(hours=1),
            "all_day": all_day, "location": location, "active": False}


def _now():
    now = time.time()
    return now, datetime.datetime.fromtimestamp(now)


async def test_alerts_when_time_to_leave(cog, cal, fake_hass):
    holder, cfg = cal
    now, now_dt = _now()
    # event in 20 min, default lead 30 → leave time was 10 min ago → alert
    holder["list"] = [_ev(now_dt + datetime.timedelta(minutes=20))]
    preds = await cog.predict_departure(fake_hass, now)
    assert len(preds) == 1
    assert preds[0]["type"] == "anticipation_departure"


async def test_no_alert_before_leave_time(cog, cal, fake_hass):
    holder, cfg = cal
    now, now_dt = _now()
    holder["list"] = [_ev(now_dt + datetime.timedelta(minutes=90))]
    assert await cog.predict_departure(fake_hass, now) == []


async def test_dedup_same_day(cog, cal, fake_hass):
    holder, cfg = cal
    now, now_dt = _now()
    holder["list"] = [_ev(now_dt + datetime.timedelta(minutes=20))]
    assert len(await cog.predict_departure(fake_hass, now)) == 1
    assert await cog.predict_departure(fake_hass, now) == []


async def test_disabled(cog, cal, fake_hass):
    holder, cfg = cal
    cfg["departure_alerts_enabled"] = False
    now, now_dt = _now()
    holder["list"] = [_ev(now_dt + datetime.timedelta(minutes=20))]
    assert await cog.predict_departure(fake_hass, now) == []


async def test_all_day_skipped(cog, cal, fake_hass):
    holder, cfg = cal
    now, now_dt = _now()
    holder["list"] = [_ev(now_dt + datetime.timedelta(minutes=20), all_day=True)]
    assert await cog.predict_departure(fake_hass, now) == []


async def test_beyond_horizon_skipped(cog, cal, fake_hass):
    holder, cfg = cal
    now, now_dt = _now()
    holder["list"] = [_ev(now_dt + datetime.timedelta(hours=5))]
    assert await cog.predict_departure(fake_hass, now) == []


async def test_no_events(cog, cal, fake_hass):
    assert await cog.predict_departure(fake_hass, time.time()) == []


async def test_travel_sensor_override_extends_lead(cog, cal, fake_hass, monkeypatch):
    holder, cfg = cal
    now, now_dt = _now()
    holder["list"] = [_ev(now_dt + datetime.timedelta(minutes=40))]
    # default lead 30 → event in 40 → leave in 10 → NO alert
    assert await cog.predict_departure(fake_hass, now) == []

    cog._RECUR_ALERTED.clear()
    cfg["departure_travel_sensor"] = "sensor.commute"

    class _St:
        state = "45"
    monkeypatch.setattr(fake_hass.states, "get",
                        lambda eid: _St() if eid == "sensor.commute" else None)
    # sensor lead = 45 + 5 buffer = 50 → leave 10 min ago → alert (sensor override wins)
    assert len(await cog.predict_departure(fake_hass, now)) == 1


async def test_uses_oss_travel_for_located_event(cog, cal, fake_hass, load, monkeypatch):
    holder, cfg = cal
    now, now_dt = _now()
    holder["list"] = [_ev(now_dt + datetime.timedelta(minutes=40), location="123 Main St")]
    monkeypatch.setattr(cog, "_current_origin", lambda hass: (40.0, -75.0))
    travel = load("travel")

    async def _tm(hass, origin, dest, osrm_url=None):
        return 45.0                                # OSS says 45 min drive
    monkeypatch.setattr(travel, "travel_minutes", _tm)
    # lead = 45 + 5 = 50 → event in 40 → leave 10 min ago → alert via OSS routing
    preds = await cog.predict_departure(fake_hass, now)
    assert len(preds) == 1 and "123 Main St" in preds[0]["message"]


async def test_no_oss_call_without_location(cog, cal, fake_hass, load, monkeypatch):
    holder, cfg = cal
    now, now_dt = _now()
    holder["list"] = [_ev(now_dt + datetime.timedelta(minutes=40))]   # no location
    monkeypatch.setattr(cog, "_current_origin", lambda hass: (40.0, -75.0))
    travel = load("travel")
    called = {"n": 0}

    async def _tm(hass, origin, dest, osrm_url=None):
        called["n"] += 1
        return 45.0
    monkeypatch.setattr(travel, "travel_minutes", _tm)
    assert await cog.predict_departure(fake_hass, now) == []          # default lead, not yet
    assert called["n"] == 0                                           # no routing without a location
