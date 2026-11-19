"""#554: the calendar range lookup (comms.list_events / agent.calendar_lookup).

Before this, JARVIS read each calendar's *attributes* (current + next event only),
so events beyond the next one were invisible and an Annuals-style entity, whose
state is a number, read back as that number instead of a date. These tests drive
the new `calendar.get_events`-backed path with a stub that filters to the
requested window, the way Home Assistant's service does.
"""
import json
from datetime import datetime, timedelta

import pytest


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _parse_dt(s):
    if not s:
        return None
    t = str(s).strip().replace(" ", "T")
    if t.endswith("Z"):
        t = t[:-1]
    try:
        return datetime.fromisoformat(t[:19] if "T" in t else t[:10])
    except Exception:
        return None


def _canned_events(mapping: dict):
    """Async stub for hass.services.async_call that answers calendar.get_events
    from {entity_id: [event dicts]}, filtered to the requested date window like
    HA does, and returns HA's {entity_id: {"events": [...]}} shape."""
    async def _call(domain, service, data=None, blocking=False,
                    return_response=False, **kw):
        if domain == "calendar" and service == "get_events":
            d = data or {}
            eid = d.get("entity_id")
            lo = _parse_dt(d.get("start_date_time"))
            hi = _parse_dt(d.get("end_date_time"))
            evs = []
            for ev in mapping.get(eid, []):
                es = _parse_dt(ev.get("start"))
                if es is None:
                    continue
                if (lo is None or es >= lo) and (hi is None or es <= hi):
                    evs.append(ev)
            return {eid: {"events": evs}}
        return None
    return _call


@pytest.fixture
def comms(load):
    return load("comms")


async def test_list_events_returns_range_with_real_dates(comms, fake_hass, monkeypatch):
    fake_hass.states.set("calendar.home", "off", friendly_name="Home")
    now = datetime.now()
    soon = now + timedelta(days=5, hours=2)
    far = now + timedelta(days=20)
    monkeypatch.setattr(fake_hass.services, "async_call", _canned_events({
        "calendar.home": [
            {"summary": "Waste collection", "start": _iso(soon),
             "end": _iso(soon + timedelta(hours=1))},
            {"summary": "Dentist", "start": _iso(far),
             "end": _iso(far + timedelta(hours=1))},
        ],
    }))
    res = await comms.list_events(fake_hass, days_ahead=31)
    assert res["count"] == 2
    # sorted by start; real dates/times surfaced
    assert res["events"][0]["title"] == "Waste collection"
    assert res["events"][0]["date"] == soon.strftime("%Y-%m-%d")
    assert res["events"][0]["time"] == soon.strftime("%H:%M")
    assert res["events"][0]["all_day"] is False


async def test_list_events_window_excludes_far_events(comms, fake_hass, monkeypatch):
    """The waste pickup next week is found; the one 20 days out is outside a
    7-day window — proving the window is really applied (#554 D)."""
    fake_hass.states.set("calendar.home", "off")
    now = datetime.now()
    near = now + timedelta(days=4, hours=1)
    far = now + timedelta(days=20)
    monkeypatch.setattr(fake_hass.services, "async_call", _canned_events({
        "calendar.home": [
            {"summary": "Bio waste", "start": _iso(near), "end": _iso(near)},
            {"summary": "Residual waste", "start": _iso(far), "end": _iso(far)},
        ],
    }))
    res = await comms.list_events(fake_hass, days_ahead=7)
    assert [e["title"] for e in res["events"]] == ["Bio waste"]


async def test_list_events_query_filters(comms, fake_hass, monkeypatch):
    fake_hass.states.set("calendar.home", "off")
    now = datetime.now()
    a, b = now + timedelta(days=3), now + timedelta(days=4)
    monkeypatch.setattr(fake_hass.services, "async_call", _canned_events({
        "calendar.home": [
            {"summary": "Bio waste", "start": _iso(a), "end": _iso(a)},
            {"summary": "Gym", "start": _iso(b), "end": _iso(b)},
        ],
    }))
    res = await comms.list_events(fake_hass, days_ahead=31, query="waste")
    assert res["count"] == 1 and res["events"][0]["title"] == "Bio waste"


async def test_list_events_all_day_birthday_is_a_date_not_a_number(comms, fake_hass, monkeypatch):
    """Annuals exposes the entity state as a number (an age); the birthday event
    itself is an all-day entry months out. A wide window surfaces the DATE."""
    fake_hass.states.set("calendar.annuals", "42", friendly_name="Annuals")
    bday = (datetime.now() + timedelta(days=120)).strftime("%Y-%m-%d")
    monkeypatch.setattr(fake_hass.services, "async_call", _canned_events({
        "calendar.annuals": [{"summary": "Sascha's birthday", "start": bday, "end": bday}],
    }))
    assert (await comms.list_events(fake_hass, days_ahead=31))["count"] == 0   # month misses it
    wide = await comms.list_events(fake_hass, days_ahead=200, query="birthday")
    assert wide["count"] == 1
    ev = wide["events"][0]
    assert ev["all_day"] is True and ev["time"] is None
    assert ev["date"] == bday           # the date, not the state "42"


async def test_list_events_no_calendars(comms, fake_hass):
    res = await comms.list_events(fake_hass, days_ahead=31)
    assert res["count"] == 0 and "error" in res


def test_calendar_entities_resolution(comms, fake_hass):
    fake_hass.states.set("calendar.home", "off", friendly_name="Family Home")
    fake_hass.states.set("calendar.work", "off", friendly_name="Work")
    assert set(comms._calendar_entities(fake_hass)) == {"calendar.home", "calendar.work"}
    assert comms._calendar_entities(fake_hass, "calendar.work") == ["calendar.work"]
    assert comms._calendar_entities(fake_hass, "family") == ["calendar.home"]  # name match
    assert comms._calendar_entities(fake_hass, "nope") == []


async def test_exec_calendar_lookup_tool(load, fake_hass, monkeypatch):
    agent = load("agent")
    fake_hass.states.set("calendar.home", "off")
    soon = datetime.now() + timedelta(days=2)
    monkeypatch.setattr(fake_hass.services, "async_call", _canned_events({
        "calendar.home": [{"summary": "Meeting", "start": _iso(soon), "end": _iso(soon)}],
    }))
    out = json.loads(await agent._exec_calendar_lookup(fake_hass, {"days_ahead": 7}))
    assert out["count"] == 1 and out["events"][0]["title"] == "Meeting"
    # registered in the dispatch map
    assert "calendar_lookup" in agent._TOOL_MAP


# ── create (#554 PR2): calendar_create_event / comms.create_event ─────────────
# The default FakeHass records service calls into .service_calls, so these assert
# the exact calendar.create_event payload without a stub.
async def test_create_event_timed(comms, fake_hass):
    fake_hass.states.set("calendar.home", "off", friendly_name="Home")
    res = await comms.create_event(fake_hass, summary="Dentist",
                                   start="2026-11-20 15:00:00")
    assert res["ok"] is True and res["calendar"] == "calendar.home"
    assert res["all_day"] is False
    dom, svc, data = fake_hass.service_calls[-1]
    assert (dom, svc) == ("calendar", "create_event")
    assert data["entity_id"] == "calendar.home" and data["summary"] == "Dentist"
    assert data["start_date_time"] == "2026-11-20 15:00:00"
    assert data["end_date_time"] == "2026-11-20 16:00:00"      # default +1h
    assert "start_date" not in data


async def test_create_event_all_day(comms, fake_hass):
    fake_hass.states.set("calendar.home", "off")
    res = await comms.create_event(fake_hass, summary="Holiday",
                                   start="2026-12-25", all_day=True)
    assert res["ok"] is True and res["all_day"] is True
    _, _, data = fake_hass.service_calls[-1]
    assert data["start_date"] == "2026-12-25"
    assert data["end_date"] == "2026-12-26"                    # exclusive end
    assert "start_date_time" not in data


async def test_create_event_bare_date_is_all_day(comms, fake_hass):
    fake_hass.states.set("calendar.home", "off")
    res = await comms.create_event(fake_hass, summary="Birthday", start="2027-03-01")
    assert res["ok"] is True and res["all_day"] is True
    _, _, data = fake_hass.service_calls[-1]
    assert data["start_date"] == "2027-03-01" and data["end_date"] == "2027-03-02"


async def test_create_event_requires_summary_and_start(comms, fake_hass):
    fake_hass.states.set("calendar.home", "off")
    assert (await comms.create_event(fake_hass, summary="",
                                     start="2026-11-20 15:00:00"))["ok"] is False
    assert (await comms.create_event(fake_hass, summary="X", start=""))["ok"] is False
    assert fake_hass.service_calls == []                       # nothing written


async def test_create_event_no_calendar(comms, fake_hass):
    res = await comms.create_event(fake_hass, summary="X", start="2026-11-20 15:00:00")
    assert res["ok"] is False and "error" in res


async def test_create_event_target_resolution(comms, fake_hass):
    fake_hass.states.set("calendar.home", "off", friendly_name="Home")
    fake_hass.states.set("calendar.work", "off", friendly_name="Work Cal")
    res = await comms.create_event(fake_hass, summary="Standup",
                                   start="2026-11-20 09:00:00", entity_id="work")
    assert res["calendar"] == "calendar.work"
    assert fake_hass.service_calls[-1][2]["entity_id"] == "calendar.work"


async def test_exec_calendar_create_event_tool(load, fake_hass):
    agent = load("agent")
    fake_hass.states.set("calendar.home", "off")
    out = json.loads(await agent._exec_calendar_create_event(fake_hass, {
        "summary": "Meeting", "start": "2026-11-21 10:00:00"}))
    assert out["ok"] is True
    assert "calendar_create_event" in agent._TOOL_MAP
