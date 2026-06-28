"""Regression tests for LocalIntentRouter.

Module-top is stdlib-only (HA bits are imported lazily inside methods), so it
loads standalone. We test the pure matching helpers and the room-context pronoun
resolution (driven by FakeHass with an injected area resolver); the HA-wired
execution path is left to integration testing.
"""
import importlib.util
import pathlib
import sys

import pytest

from fakes import FakeHass

COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"
AREA = "office"


def _load_standalone(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, COMP / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ir = _load_standalone("jarvis_intent_router", "intent/intent_router.py")


@pytest.mark.parametrize("phrase,expected", [
    ("secure the garage", "secure_area"),
    ("close the garage door", "secure_area"),
    ("turn off the lights", "lights_off"),
    ("lights off please", "lights_off"),
    ("kill the lights", "lights_off"),
    ("turn on the lights", "lights_on"),
    ("turn it off", "context_off"),
    ("shut it off", "context_off"),
    ("pause it", "context_off"),
    ("close it", "context_close"),
    ("what's the weather", None),
    ("", None),
])
def test_match_intent(phrase, expected):
    out = ir.match_intent(phrase)
    assert (out["intent"] if out else None) == expected


@pytest.mark.parametrize("phrase,expected", [
    ("yes", True),
    ("yeah do it", True),
    ("go ahead", True),
    ("affirmative", True),
    ("okay", True),
    ("close it", True),
    ("no", False),
    ("cancel that", False),
    ("turn on the lights", False),
    ("", False),
])
def test_is_affirmative(phrase, expected):
    assert ir.is_affirmative(phrase) is expected


def _router(states: dict):
    hass = FakeHass()
    for eid, (state, attrs) in states.items():
        hass.states.set(eid, state, **attrs)
    return ir.LocalIntentRouter(hass), hass


def _office_area(_eid):
    return AREA


def test_resolve_prefers_playing_media():
    r, _ = _router({
        "media_player.office_speaker": ("playing", {}),
        "light.office_lamp": ("on", {}),
    })
    eid, domain = r.resolve_active_entity(AREA, area_of=_office_area)
    assert domain == "media_player"
    assert eid == "media_player.office_speaker"


def test_resolve_falls_back_to_light():
    r, _ = _router({
        "media_player.office_speaker": ("paused", {}),  # not playing
        "light.office_lamp": ("on", {}),
    })
    eid, domain = r.resolve_active_entity(AREA, area_of=_office_area)
    assert domain == "light"
    assert eid == "light.office_lamp"


def test_resolve_nothing_active():
    r, _ = _router({
        "media_player.office_speaker": ("idle", {}),
        "light.office_lamp": ("off", {}),
    })
    assert r.resolve_active_entity(AREA, area_of=_office_area) == (None, None)


def test_resolve_is_area_scoped():
    # An active light in a different area must not resolve for the office.
    def area_of(eid):
        return "kitchen" if "kitchen" in eid else "office"

    r, _ = _router({"light.kitchen_lamp": ("on", {})})
    assert r.resolve_active_entity(AREA, area_of=area_of) == (None, None)


def test_router_accepts_optional_ledger():
    # The router takes an injected ledger (duck-typed) without importing it, so
    # the pure matching/resolution paths stay standalone-loadable.
    class FakeLedger:
        def record_intent(self, *a, **k):
            return "txn"

        def mark_complete(self, *a, **k):
            pass

    r = ir.LocalIntentRouter(FakeHass(), ledger=FakeLedger())
    assert r.ledger is not None
    assert ir.match_intent("secure the garage")["intent"] == "secure_area"
