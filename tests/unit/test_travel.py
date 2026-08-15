"""Tests for open-source travel time (travel, v6.89.0) — pure parsers + the
geocode→route orchestration with the network layer mocked."""
import pytest


@pytest.fixture
def travel(load):
    t = load("travel")
    t._GEO_CACHE.clear()
    return t


def test_coords_from_str(travel):
    assert travel._coords_from_str("40.7,-74.0") == (40.7, -74.0)
    assert travel._coords_from_str("  12.5 , 13.25 ") == (12.5, 13.25)
    assert travel._coords_from_str("Main Street") is None
    assert travel._coords_from_str("") is None


def test_parse_geocode(travel):
    assert travel._parse_geocode([{"lat": "40.71", "lon": "-74.01"}]) == (40.71, -74.01)
    assert travel._parse_geocode([]) is None
    assert travel._parse_geocode(None) is None
    assert travel._parse_geocode([{"nope": 1}]) is None


def test_route_url_lon_lat_order(travel):
    url = travel._route_url((40.0, -75.0), (41.0, -74.0), "https://osrm.example")
    assert url == ("https://osrm.example/route/v1/driving/"
                   "-75.000000,40.000000;-74.000000,41.000000?overview=false")


def test_route_url_default_base(travel):
    assert travel._route_url((1.0, 2.0), (3.0, 4.0)).startswith(travel.DEFAULT_OSRM)


def test_parse_route(travel):
    assert travel._parse_route({"code": "Ok", "routes": [{"duration": 600}]}) == 10.0
    assert travel._parse_route({"code": "NoRoute"}) is None
    assert travel._parse_route({}) is None
    assert travel._parse_route(None) is None


async def test_travel_minutes_geocode_then_route(travel, fake_hass, monkeypatch):
    calls = []

    async def _fake_get(hass, url, params=None):
        calls.append(url)
        if url == travel.NOMINATIM_URL:
            return [{"lat": "41.0", "lon": "-74.0"}]
        return {"code": "Ok", "routes": [{"duration": 1200}]}
    monkeypatch.setattr(travel, "_get_json", _fake_get)
    mins = await travel.travel_minutes(fake_hass, (40.0, -75.0), "123 Main St")
    assert mins == 20.0
    assert calls[0] == travel.NOMINATIM_URL               # geocoded first
    assert any("route/v1" in u for u in calls)            # then routed


async def test_travel_minutes_caches_geocode(travel, fake_hass, monkeypatch):
    geo = {"n": 0}

    async def _fake_get(hass, url, params=None):
        if url == travel.NOMINATIM_URL:
            geo["n"] += 1
            return [{"lat": "41.0", "lon": "-74.0"}]
        return {"code": "Ok", "routes": [{"duration": 600}]}
    monkeypatch.setattr(travel, "_get_json", _fake_get)
    await travel.travel_minutes(fake_hass, (40.0, -75.0), "Same Place")
    await travel.travel_minutes(fake_hass, (40.0, -75.0), "Same Place")
    assert geo["n"] == 1                                   # second call hit the cache


async def test_coord_dest_skips_geocode(travel, fake_hass, monkeypatch):
    async def _fake_get(hass, url, params=None):
        assert url != travel.NOMINATIM_URL                # never geocode a coord literal
        return {"code": "Ok", "routes": [{"duration": 300}]}
    monkeypatch.setattr(travel, "_get_json", _fake_get)
    assert await travel.travel_minutes(fake_hass, (40.0, -75.0), "41.0,-74.0") == 5.0


async def test_geocode_fail_returns_none(travel, fake_hass, monkeypatch):
    async def _fake_get(hass, url, params=None):
        return None
    monkeypatch.setattr(travel, "_get_json", _fake_get)
    assert await travel.travel_minutes(fake_hass, (40.0, -75.0), "Nowhere") is None


async def test_no_origin_or_dest_returns_none(travel, fake_hass):
    assert await travel.travel_minutes(fake_hass, None, "X") is None
    assert await travel.travel_minutes(fake_hass, (1.0, 2.0), "") is None
