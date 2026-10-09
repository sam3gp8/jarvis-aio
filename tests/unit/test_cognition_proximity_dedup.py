"""Arrival anticipation fires ONCE per person, not once per presence entity (#265).

A `person` entity and the `device_tracker`s it owns (its "Track these devices"
list) are all GPS presence sources, so `cognition.predict_proximity` used to emit
"heading home" for the person AND each tracker ("brewston" + "brewston_S26"). It
is now deduped by the owning person, and names the person even when a tracker is
the source that crossed the threshold first.
"""
import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from fakes import FakeHass

COMP = Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"


@pytest.fixture
def cog():
    if "jc" not in sys.modules:
        pkg = types.ModuleType("jc")
        pkg.__path__ = [str(COMP)]
        sys.modules["jc"] = pkg
    jcfg = importlib.import_module("jc.jarvis_config")
    jcfg._loaded = True
    jcfg._cache = {}
    sys.modules.pop("jc.cognition", None)   # fresh module state (_LAST_DIST, …)
    spec = importlib.util.spec_from_file_location("jc.cognition", COMP / "cognition.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jc.cognition"] = mod
    spec.loader.exec_module(mod)
    return mod


def _patch_dist(cog, monkeypatch, dist):
    monkeypatch.setattr(cog, "distance_home_km",
                        lambda hass, st: dist.get(st.entity_id))


def test_arrival_fires_once_per_person(cog, monkeypatch):
    hass = FakeHass()
    trackers = ["device_tracker.daughters_phone", "device_tracker.googlemaps_daughter"]
    hass.states.set("person.daughter", "not_home", latitude=1.0, longitude=1.0,
                    friendly_name="Daughter", device_trackers=trackers)
    hass.states.set("device_tracker.daughters_phone", "not_home",
                    latitude=1.0, longitude=1.0, friendly_name="Daughters Phone")
    hass.states.set("device_tracker.googlemaps_daughter", "not_home",
                    latitude=1.0, longitude=1.0, friendly_name="GMaps Daughter")

    dist = {"person.daughter": 3.0, "device_tracker.daughters_phone": 3.0,
            "device_tracker.googlemaps_daughter": 3.0}
    _patch_dist(cog, monkeypatch, dist)

    assert cog.predict_proximity(hass) == []          # first pass seeds direction
    for k in dist:
        dist[k] = 1.0                                  # everyone now closing on home
    out = cog.predict_proximity(hass)

    assert len(out) == 1                               # one person → one alert
    assert out[0]["pattern_key"] == "arriving:person.daughter"
    assert "Daughter is heading home" in out[0]["message"]  # the person, not a phone
    assert out[0]["push"] is True                      # push-eligible (#265)


def test_standalone_tracker_still_alerts(cog, monkeypatch):
    # A tracker not attached to any person is its own mover and still alerts.
    hass = FakeHass()
    hass.states.set("device_tracker.guest_phone", "not_home",
                    latitude=1.0, longitude=1.0, friendly_name="Guest Phone")
    dist = {"device_tracker.guest_phone": 4.0}
    _patch_dist(cog, monkeypatch, dist)

    assert cog.predict_proximity(hass) == []
    dist["device_tracker.guest_phone"] = 2.0
    out = cog.predict_proximity(hass)
    assert len(out) == 1
    assert out[0]["pattern_key"] == "arriving:device_tracker.guest_phone"


def test_reaching_home_resets_so_next_trip_alerts_again(cog, monkeypatch):
    hass = FakeHass()
    hass.states.set("person.p", "not_home", latitude=1.0, longitude=1.0,
                    friendly_name="P", device_trackers=["device_tracker.p_phone"])
    hass.states.set("device_tracker.p_phone", "not_home",
                    latitude=1.0, longitude=1.0, friendly_name="P phone")
    dist = {"person.p": 3.0, "device_tracker.p_phone": 3.0}
    _patch_dist(cog, monkeypatch, dist)

    cog.predict_proximity(hass)                        # seed
    for k in dist:
        dist[k] = 1.0
    assert len(cog.predict_proximity(hass)) == 1       # alerted this trip
    assert cog.predict_proximity(hass) == []           # still closing, no repeat

    # Arrive home → the person's approach state clears.
    hass.states.set("person.p", "home", latitude=1.0, longitude=1.0,
                    friendly_name="P", device_trackers=["device_tracker.p_phone"])
    hass.states.set("device_tracker.p_phone", "home",
                    latitude=1.0, longitude=1.0, friendly_name="P phone")
    cog.predict_proximity(hass)

    # Leave again and close in → a fresh alert is allowed.
    hass.states.set("person.p", "not_home", latitude=1.0, longitude=1.0,
                    friendly_name="P", device_trackers=["device_tracker.p_phone"])
    hass.states.set("device_tracker.p_phone", "not_home",
                    latitude=1.0, longitude=1.0, friendly_name="P phone")
    dist = {"person.p": 3.0, "device_tracker.p_phone": 3.0}
    _patch_dist(cog, monkeypatch, dist)
    cog.predict_proximity(hass)                        # seed again
    for k in dist:
        dist[k] = 1.0
    assert len(cog.predict_proximity(hass)) == 1       # new trip alerts again
