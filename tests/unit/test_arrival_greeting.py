"""Arrival greeting (v9.84.0): identify a resident by recognition-or-presence,
greet by name within chosen hours, stay silent when unknown, per-person cooldown.
All logic here is pure/defensive; the HA door listener lives in __init__.py."""
import sys
import types
from datetime import datetime, timezone, timedelta

import pytest


@pytest.fixture
def ag(load):
    sys.modules.pop("jc.arrival_greeting", None)
    mod = load("arrival_greeting")
    mod.reset_cooldowns()
    mod.reset_departures()
    yield mod
    mod.reset_cooldowns()
    mod.reset_departures()
    sys.modules.pop("jc.arrival_greeting", None)


# ── within_hours ─────────────────────────────────────────────────────────────
def test_within_hours_blank_is_always(ag):
    assert ag.within_hours(13 * 60, "", "") is True


def test_within_hours_normal_window(ag):
    assert ag.within_hours(9 * 60, "08:00", "17:00") is True
    assert ag.within_hours(20 * 60, "08:00", "17:00") is False


def test_within_hours_wraps_midnight(ag):
    assert ag.within_hours(23 * 60, "22:00", "06:00") is True
    assert ag.within_hours(3 * 60, "22:00", "06:00") is True
    assert ag.within_hours(12 * 60, "22:00", "06:00") is False


def test_within_hours_single_bound(ag):
    assert ag.within_hours(10 * 60, "08:00", "") is True    # after start, no end
    assert ag.within_hours(7 * 60, "08:00", "") is False
    assert ag.within_hours(5 * 60, "", "06:00") is True     # before end, no start


# ── greeting_text ────────────────────────────────────────────────────────────
def test_greeting_text(ag):
    assert ag.greeting_text("Sam", "Good evening") == "Good evening, Sam. Welcome home."
    assert ag.greeting_text("Sam", "") == "Welcome home, Sam."
    assert ag.greeting_text("", "Good morning") == ""


# ── cooldown ─────────────────────────────────────────────────────────────────
def test_allow_greeting_cooldown(ag):
    assert ag.allow_greeting("Sam", 30, now=1000.0) is True            # first time
    assert ag.allow_greeting("Sam", 30, now=1000.0 + 60) is False      # within 30 min
    assert ag.allow_greeting("Sam", 30, now=1000.0 + 31 * 60) is True  # past cooldown
    assert ag.allow_greeting("Alex", 30, now=1000.0 + 61) is True      # other person independent
    assert ag.allow_greeting("Pat", 0, now=3000.0) is True             # zero cooldown: always,
    assert ag.allow_greeting("Pat", 0, now=3000.1) is True             # as long as time moves on


# ── identify_arrival (recognition → presence) ────────────────────────────────
class _St:
    def __init__(self, entity_id, state, age_secs=10, friendly=None):
        self.entity_id = entity_id
        self.state = state
        self.last_changed = datetime.now(timezone.utc) - timedelta(seconds=age_secs)
        self.attributes = {"friendly_name": friendly} if friendly else {}


class _Hass:
    def __init__(self, by_domain):
        self._by = by_domain

    class _S:
        def __init__(self, by):
            self._by = by

        def async_all(self, domain):
            return self._by.get(domain, [])

    @property
    def states(self):
        return _Hass._S(self._by)


def _stub_recognition(monkeypatch, name):
    rec = types.ModuleType("jc.recognition")
    rec.resident_present = lambda hass, **k: name
    monkeypatch.setitem(sys.modules, "jc.recognition", rec)


def _stub_roster(monkeypatch, residents):
    fr = types.ModuleType("jc.face_roster")
    _set = {r.lower() for r in residents}
    fr.is_resident = lambda n: str(n).strip().lower() in _set
    monkeypatch.setitem(sys.modules, "jc.face_roster", fr)


def test_identify_prefers_recognition(ag, monkeypatch):
    _stub_recognition(monkeypatch, "Sam")     # recognition already resident-gated
    assert ag.identify_arrival(_Hass({})) == "Sam"


def test_identify_presence_fallback(ag, monkeypatch):
    _stub_recognition(monkeypatch, None)      # recognition finds nobody
    _stub_roster(monkeypatch, ["Sam"])
    hass = _Hass({"person": [_St("person.sam", "home", age_secs=10, friendly="Sam")]})
    assert ag.identify_arrival(hass, within_secs=120) == "Sam"


def test_identify_presence_requires_resident(ag, monkeypatch):
    _stub_recognition(monkeypatch, None)
    _stub_roster(monkeypatch, ["Sam"])        # "Guest" is not a flagged resident
    hass = _Hass({"person": [_St("person.guest", "home", age_secs=10, friendly="Guest")]})
    assert ag.identify_arrival(hass) is None   # stays silent


def test_identify_ignores_stale_home(ag, monkeypatch):
    _stub_recognition(monkeypatch, None)
    _stub_roster(monkeypatch, ["Sam"])
    hass = _Hass({"person": [_St("person.sam", "home", age_secs=99999, friendly="Sam")]})
    assert ag.identify_arrival(hass, within_secs=120) is None   # arrived too long ago


def test_identify_none_when_nothing(ag, monkeypatch):
    _stub_recognition(monkeypatch, None)
    _stub_roster(monkeypatch, ["Sam"])
    assert ag.identify_arrival(_Hass({})) is None


# ── departure ledger: only greet a genuine leave→return ──────────────────────
def test_departure_gate_requires_leaving_first(ag):
    assert ag.has_departed("Sam") is False        # home all along → never greeted
    ag.note_departure("Sam", now=100.0)
    assert ag.has_departed("Sam") is True          # left the property → greetable
    ag.clear_departure("Sam")                      # greeted → consume
    assert ag.has_departed("Sam") is False         # next greeting needs a fresh leave→return


def test_departure_is_case_insensitive(ag):
    ag.note_departure("SAM")
    assert ag.has_departed("sam") is True
    assert ag.has_departed("  Sam ") is True


def test_departure_is_per_person(ag):
    ag.note_departure("Sam")
    assert ag.has_departed("Alex") is False         # independent per person


def test_departure_blank_name_is_noop(ag):
    ag.note_departure("")
    ag.note_departure(None)
    assert ag.has_departed("") is False
    ag.clear_departure("never-left")                # harmless no-op


def test_reset_departures_clears(ag):
    ag.note_departure("Sam")
    ag.reset_departures()
    assert ag.has_departed("Sam") is False
