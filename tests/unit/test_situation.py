"""Tests for the situational snapshot (situation, v6.87.0) — the live composite
(time, presence, weather, calendar, energy, recent activity) fed to the agent."""
import pytest


@pytest.fixture
def sit(load):
    return load("situation")


@pytest.fixture
def set_observer(monkeypatch):
    """Inject a fake jc.observer so situation._activity resolves it in isolation."""
    import sys
    import types

    def _set(recent):
        mod = types.ModuleType("jc.observer")
        mod.get_recent_context = lambda s=600, _r=recent: _r
        monkeypatch.setitem(sys.modules, "jc.observer", mod)
        # `from . import observer` binds the package attribute, so override that
        # too — otherwise a real observer imported by an earlier test wins.
        pkg = sys.modules.get("jc")
        if pkg is not None:
            monkeypatch.setattr(pkg, "observer", mod, raising=False)
    return _set


def test_time_includes_full_date(sit, fake_hass):
    # The situational time line must carry the full date — weekday, month, day
    # and YEAR — so the agent (and web search) knows the current date rather than
    # defaulting to the model's training cutoff. Regression for discussion #55.
    import datetime as _dt
    out = sit._time(fake_hass)
    now = _dt.datetime.now()
    assert out.startswith("Time:")
    assert str(now.year) in out                 # the year is present
    assert now.strftime("%A") in out            # weekday
    assert now.strftime("%B") in out            # month name


def test_weather_reads_entity(sit, fake_hass, monkeypatch):
    class _St:
        state = "sunny"
        attributes = {"temperature": 72, "temperature_unit": "F"}
    monkeypatch.setattr(fake_hass.states, "async_all",
                        lambda d=None: [_St()] if d == "weather" else [])
    assert sit._weather(fake_hass) == "Weather: sunny, 72F"


def test_weather_none(sit, fake_hass, monkeypatch):
    monkeypatch.setattr(fake_hass.states, "async_all", lambda d=None: [])
    assert sit._weather(fake_hass) == ""


def test_energy_formats_kw(sit, fake_hass, load, monkeypatch):
    energy = load("energy")
    monkeypatch.setattr(energy, "power_status", lambda hass: {"kw": 1.2, "over_peak": False})
    assert sit._energy(fake_hass) == "Power: 1.2 kW"
    monkeypatch.setattr(energy, "power_status", lambda hass: {"kw": 8.5, "over_peak": True})
    assert "over peak" in sit._energy(fake_hass)


def test_energy_no_meter_omitted(sit, fake_hass, load, monkeypatch):
    energy = load("energy")
    monkeypatch.setattr(energy, "power_status", lambda hass: {"kw": None})
    assert sit._energy(fake_hass) == ""


def test_calendar_from_agenda(sit, fake_hass, load, monkeypatch):
    comms = load("comms")
    monkeypatch.setattr(comms, "agenda", lambda hass, h=24: {
        "events": ["Dentist - 9:00 AM", "Lunch - 12:00 PM", "Extra - 3 PM"],
        "conflicts": ["tight: only 10 min between A and B"], "count": 3})
    out = sit._calendar(fake_hass)
    assert "Next up: Dentist - 9:00 AM; Lunch - 12:00 PM" in out    # first two only
    assert "tight" in out                                           # first conflict


def test_calendar_empty_omitted(sit, fake_hass, load, monkeypatch):
    comms = load("comms")
    monkeypatch.setattr(comms, "agenda", lambda hass, h=24: {"events": [], "conflicts": [], "count": 0})
    assert sit._calendar(fake_hass) == ""


def test_presence_line(sit, fake_hass, load, monkeypatch):
    presence = load("presence")
    monkeypatch.setattr(presence, "presence_context_string", lambda hass: "Sam is home.")
    assert sit._presence(fake_hass) == "Presence: Sam is home."


def test_presence_no_entities_omitted(sit, fake_hass, load, monkeypatch):
    presence = load("presence")
    monkeypatch.setattr(presence, "presence_context_string",
                        lambda hass: "No person entities configured in Home Assistant.")
    assert sit._presence(fake_hass) == ""


def test_activity_omits_quiet(sit, fake_hass, set_observer):
    set_observer("quiet - no notable recent activity")
    assert sit._activity(fake_hass) == ""


def test_activity_summarizes(sit, fake_hass, set_observer):
    set_observer("  [2m ago] Kitchen light: off -> on\n"
                 "  [1m ago] Front door: closed -> open")
    out = sit._activity(fake_hass)
    assert out.startswith("Recent activity:") and "Front door" in out


def test_snapshot_composites(sit, fake_hass, load, monkeypatch, set_observer):
    presence = load("presence"); comms = load("comms"); energy = load("energy")
    monkeypatch.setattr(presence, "presence_context_string", lambda hass: "Sam is home.")
    monkeypatch.setattr(comms, "agenda",
                        lambda hass, h=24: {"events": ["Dentist - 9 AM"], "conflicts": [], "count": 1})
    monkeypatch.setattr(energy, "power_status", lambda hass: {"kw": 1.2, "over_peak": False})
    set_observer("quiet")
    monkeypatch.setattr(fake_hass.states, "async_all", lambda d=None: [])
    out = sit.snapshot(fake_hass)
    assert out.startswith("Time:")                 # time leads
    assert "Presence: Sam is home." in out
    assert "Next up: Dentist - 9 AM" in out
    assert "Power: 1.2 kW" in out


def test_snapshot_never_raises_on_bad_source(sit, fake_hass, load, monkeypatch):
    presence = load("presence")

    def boom(hass):
        raise RuntimeError("presence exploded")
    monkeypatch.setattr(presence, "presence_context_string", boom)
    monkeypatch.setattr(fake_hass.states, "async_all", lambda d=None: [])
    out = sit.snapshot(fake_hass)                    # must not raise
    assert "Presence:" not in out                   # bad source omitted


def test_time_falls_back_when_ha_clock_unavailable(sit, fake_hass, monkeypatch):
    import homeassistant.util.dt as dt_util
    monkeypatch.setattr(dt_util, "now",
                        lambda: (_ for _ in ()).throw(RuntimeError("no clock")))
    out = sit._time(fake_hass)
    assert out.startswith("Time:")


def test_weather_condition_without_temperature(sit, fake_hass, monkeypatch):
    class _St:
        state = "partly_cloudy"
        attributes = {}
    monkeypatch.setattr(fake_hass.states, "async_all", lambda d=None: [_St()])
    assert sit._weather(fake_hass) == "Weather: partly cloudy"


def test_weather_failure_is_omitted(sit, fake_hass, monkeypatch):
    monkeypatch.setattr(fake_hass.states, "async_all",
                        lambda d=None: (_ for _ in ()).throw(RuntimeError("state failure")))
    assert sit._weather(fake_hass) == ""


def test_calendar_includes_first_conflict(sit, fake_hass, load, monkeypatch):
    comms = load("comms")
    monkeypatch.setattr(comms, "agenda", lambda hass, h=12: {
        "events": ["Meeting"], "conflicts": ["overlap"]})
    assert sit._calendar(fake_hass) == "Next up: Meeting (overlap)"


def test_activity_keeps_only_last_three_lines(sit, fake_hass, set_observer):
    set_observer("one\ntwo\nthree\nfour")
    assert sit._activity(fake_hass) == "Recent activity: two; three; four"
