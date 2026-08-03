"""Tests for the multi-hazard monitor (v6.71.0). This is integration-boundary
code (USGS/NWS/EONET live feeds), so we test the pure logic that carries the
correctness weight against mocked JSON: distance math, bounding boxes, per-feed
dedup, response parsing, and severity/magnitude/radius filtering. The network
call itself (_get_json) is stubbed."""
import importlib.util
import pathlib
import sys
import types

import pytest

COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"


@pytest.fixture
def hz(monkeypatch):
    """Load hazard_monitor with a stub parent package (its `from . import
    jarvis_config` must resolve). Uses monkeypatch for all sys.modules / package
    attribute changes so pytest restores them and this fixture can't pollute
    other test files (the jc-package-attribute isolation trap)."""
    if "jc" not in sys.modules:
        pkg = types.ModuleType("jc")
        pkg.__path__ = [str(COMP)]
        monkeypatch.setitem(sys.modules, "jc", pkg)
    cfg_store = {}
    jc_cfg = types.ModuleType("jc.jarvis_config")
    jc_cfg.get = lambda k, d=None: cfg_store.get(k, d)
    monkeypatch.setitem(sys.modules, "jc.jarvis_config", jc_cfg)
    # `from . import jarvis_config` reads the jc package ATTRIBUTE, so patch it
    # too — monkeypatch restores the prior value (or removes it) after the test.
    monkeypatch.setattr(sys.modules["jc"], "jarvis_config", jc_cfg, raising=False)
    key = "jc.hazard_monitor"
    monkeypatch.delitem(sys.modules, key, raising=False)
    spec = importlib.util.spec_from_file_location(key, COMP / "hazard_monitor.py")
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, key, mod)
    spec.loader.exec_module(mod)
    mod._cfg_store = cfg_store
    mod._SEEN["quake"].clear(); mod._SEEN["wx"].clear(); mod._SEEN["disaster"].clear()
    return mod


class _Hass:
    class config:
        latitude = 40.77      # Slatington-ish PA
        longitude = -75.61
    class states:
        @staticmethod
        def get(_): return None


# ── geo math ─────────────────────────────────────────────────────────────────

def test_haversine_zero_distance(hz):
    assert hz._haversine_km(40.0, -75.0, 40.0, -75.0) == pytest.approx(0.0, abs=0.01)


def test_haversine_known_distance(hz):
    # NYC (40.71,-74.01) to Philadelphia (39.95,-75.16) ≈ 130 km
    d = hz._haversine_km(40.71, -74.01, 39.95, -75.16)
    assert 120 < d < 145


def test_bbox_brackets_point(hz):
    min_lat, max_lat, min_lon, max_lon = hz._bbox(40.0, -75.0, 100.0)
    assert min_lat < 40.0 < max_lat
    assert min_lon < -75.0 < max_lon
    # ~100km ≈ ~0.9deg lat
    assert 0.7 < (max_lat - 40.0) < 1.1


# ── location resolution ──────────────────────────────────────────────────────

def test_home_latlon_uses_ha_config(hz):
    lat, lon = hz._home_latlon(_Hass())
    assert lat == pytest.approx(40.77) and lon == pytest.approx(-75.61)


def test_home_latlon_override_wins(hz):
    hz._cfg_store["hazard_lat"] = 34.05
    hz._cfg_store["hazard_lon"] = -118.24
    lat, lon = hz._home_latlon(_Hass())
    assert lat == pytest.approx(34.05) and lon == pytest.approx(-118.24)


# ── dedup ────────────────────────────────────────────────────────────────────

def test_remember_new_then_seen(hz):
    assert hz._remember("quake", "us123") is True     # first time = new
    assert hz._remember("quake", "us123") is False    # second time = seen


# ── earthquake feed parsing + filtering ──────────────────────────────────────

async def test_earthquakes_parsed_and_distance_filtered(hz, monkeypatch):
    # one quake ~within radius, one far away → only the near one returns
    async def _fake_get(hass, url, params=None):
        return {"features": [
            {"id": "near1", "properties": {"mag": 3.2, "place": "10km N of home",
             "url": "http://u/near1"},
             "geometry": {"coordinates": [-75.60, 40.80]}},   # ~4km away
            {"id": "far1", "properties": {"mag": 6.0, "place": "far away"},
             "geometry": {"coordinates": [-120.0, 35.0]}},    # thousands of km
        ]}
    monkeypatch.setattr(hz, "_get_json", _fake_get)
    out = await hz._check_earthquakes(_Hass(), 40.77, -75.61)
    ids = [q["id"] for q in out]
    assert "near1" in ids
    assert "far1" not in ids                 # outside radius, excluded
    assert out[0]["mag"] == 3.2


async def test_earthquakes_dedup_no_repeat(hz, monkeypatch):
    async def _fake_get(hass, url, params=None):
        return {"features": [
            {"id": "q9", "properties": {"mag": 4.0, "place": "here"},
             "geometry": {"coordinates": [-75.61, 40.78]}},
        ]}
    monkeypatch.setattr(hz, "_get_json", _fake_get)
    first = await hz._check_earthquakes(_Hass(), 40.77, -75.61)
    second = await hz._check_earthquakes(_Hass(), 40.77, -75.61)
    assert len(first) == 1 and len(second) == 0    # same quake not re-reported


async def test_earthquakes_empty_on_no_data(hz, monkeypatch):
    async def _fake_get(hass, url, params=None): return None
    monkeypatch.setattr(hz, "_get_json", _fake_get)
    assert await hz._check_earthquakes(_Hass(), 40.77, -75.61) == []


# ── weather feed: severity filtering ─────────────────────────────────────────

async def test_weather_filters_by_severity(hz, monkeypatch):
    async def _fake_get(hass, url, params=None):
        return {"features": [
            {"id": "wx-severe", "properties": {"event": "Tornado Warning",
             "severity": "Extreme", "areaDesc": "Lehigh, PA", "instruction": "Take cover"}},
            {"id": "wx-minor", "properties": {"event": "Frost Advisory",
             "severity": "Minor", "areaDesc": "Lehigh, PA"}},
        ]}
    monkeypatch.setattr(hz, "_get_json", _fake_get)
    out = await hz._check_weather(_Hass(), 40.77, -75.61)
    ids = [w["id"] for w in out]
    assert "wx-severe" in ids            # Extreme passes
    assert "wx-minor" not in ids         # Minor filtered out by default


async def test_weather_custom_severities(hz, monkeypatch):
    hz._cfg_store["hazard_wx_severities"] = ["Minor"]   # only want minor
    async def _fake_get(hass, url, params=None):
        return {"features": [
            {"id": "m1", "properties": {"event": "Advisory", "severity": "Minor"}},
            {"id": "e1", "properties": {"event": "Warning", "severity": "Extreme"}},
        ]}
    monkeypatch.setattr(hz, "_get_json", _fake_get)
    out = await hz._check_weather(_Hass(), 40.77, -75.61)
    assert [w["id"] for w in out] == ["m1"]


# ── disaster (EONET) feed ────────────────────────────────────────────────────

async def test_disasters_nearest_geometry_and_radius(hz, monkeypatch):
    async def _fake_get(hass, url, params=None):
        return {"events": [
            {"id": "EONET_near", "title": "Wildfire XYZ",
             "categories": [{"title": "Wildfires"}],
             "geometry": [{"coordinates": [-75.55, 40.70]}],     # ~10km
             "sources": [{"url": "http://s/near"}]},
            {"id": "EONET_far", "title": "Volcano ABC",
             "categories": [{"title": "Volcanoes"}],
             "geometry": [{"coordinates": [130.0, 33.0]}]},      # far
        ]}
    monkeypatch.setattr(hz, "_get_json", _fake_get)
    out = await hz._check_disasters(_Hass(), 40.77, -75.61)
    ids = [d["id"] for d in out]
    assert "EONET_near" in ids
    assert "EONET_far" not in ids
    assert out[0]["category"] == "Wildfires"


# ── on-demand scan doesn't consume dedup ─────────────────────────────────────

async def test_scan_now_does_not_consume_dedup(hz, monkeypatch):
    async def _fake_get(hass, url, params=None):
        if "earthquake" in url:
            return {"features": [
                {"id": "sq1", "properties": {"mag": 3.0, "place": "x"},
                 "geometry": {"coordinates": [-75.61, 40.78]}}]}
        return {"features": [], "events": []}
    monkeypatch.setattr(hz, "_get_json", _fake_get)
    # a manual scan should see the quake...
    scan = await hz.scan_now(_Hass())
    assert scan["counts"]["earthquakes"] == 1
    # ...but must NOT have marked it seen, so the background check still fires it
    bg = await hz._check_earthquakes(_Hass(), 40.77, -75.61)
    assert len(bg) == 1


# ── periodic_check respects the master switch ────────────────────────────────

async def test_periodic_check_disabled_by_default(hz):
    res = await hz.periodic_check(_Hass())
    assert res.get("skipped") == "disabled"


async def test_status_reports_config(hz):
    hz._cfg_store["hazard_monitor_enabled"] = True
    st = await hz.status(_Hass())
    assert st["enabled"] is True
    assert st["center"] == [40.77, -75.61]
    assert st["feeds"]["earthquakes"] is True
