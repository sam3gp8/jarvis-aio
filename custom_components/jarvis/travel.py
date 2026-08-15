"""
JARVIS — open-source travel time (v6.89.0).

Computes drive time from where you are now to a calendar event's location using
device tracking + open-source services only — no Google Travel Time (a paid API)
and no Waze (both need a static origin+destination per integration instance,
forcing one instance per place). Origin comes from the person's live GPS; the
event's location string is geocoded via Nominatim (OpenStreetMap) and routed via
OSRM. Both are keyless; OSRM's base URL is configurable so it can point at a
self-hosted server.

All network I/O is async (HA's shared aiohttp session, short timeout) and
returns None on any failure, so the caller falls back to a fixed lead. Geocoding
results are cached in-process to respect Nominatim's usage policy. The response
parsers are pure and unit-tested without touching the network.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

_LOGGER = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
DEFAULT_OSRM = "https://router.project-osrm.org"
_TIMEOUT = 8
_UA = "jarvis-home-assistant (Home Assistant integration)"
_GEO_CACHE: dict = {}          # normalized location string -> (lat, lon)
_COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


def _coords_from_str(s: str):
    """A 'lat,lon' string -> (lat, lon); else None (needs geocoding)."""
    m = _COORD_RE.match(str(s or ""))
    if not m:
        return None
    try:
        return float(m.group(1)), float(m.group(2))
    except ValueError:
        return None


def _parse_geocode(data) -> Optional[tuple]:
    """First Nominatim hit -> (lat, lon). Pure."""
    try:
        if isinstance(data, list) and data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _route_url(origin, dest, base: str = None) -> str:
    """OSRM driving-route URL. OSRM expects lon,lat order."""
    base = (base or DEFAULT_OSRM).rstrip("/")
    return ("%s/route/v1/driving/%f,%f;%f,%f?overview=false"
            % (base, origin[1], origin[0], dest[1], dest[0]))


def _parse_route(data) -> Optional[float]:
    """OSRM response -> drive minutes. Pure."""
    try:
        if (data or {}).get("code") == "Ok":
            secs = data["routes"][0]["duration"]
            return round(float(secs) / 60.0, 1)
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return None


async def _get_json(hass, url: str, params: dict = None):
    """GET url -> parsed JSON, or None. Async, short timeout, UA set."""
    try:
        import aiohttp
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        session = async_get_clientsession(hass)
        async with session.get(
            url, params=params, headers={"User-Agent": _UA},
            timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                return None
            return await resp.json(content_type=None)
    except Exception as exc:
        _LOGGER.debug("travel _get_json(%s) failed: %s", url, exc)
        return None


async def _geocode(hass, location: str) -> Optional[tuple]:
    """Geocode a place string to (lat, lon) via Nominatim, cached. None on miss."""
    key = (location or "").strip().lower()
    if not key:
        return None
    if key in _GEO_CACHE:
        return _GEO_CACHE[key]
    data = await _get_json(hass, NOMINATIM_URL,
                           {"q": location, "format": "json", "limit": "1"})
    coords = _parse_geocode(data)
    if coords:
        _GEO_CACHE[key] = coords
    return coords


async def travel_minutes(hass, origin, dest_location: str,
                         osrm_url: str = None) -> Optional[float]:
    """Drive minutes from origin (lat, lon) to dest_location (a place string or
    'lat,lon'), via open-source geocode + route. None on any failure so the
    caller can fall back to a fixed lead."""
    if not origin or not dest_location:
        return None
    dest = _coords_from_str(dest_location) or await _geocode(hass, dest_location)
    if not dest:
        return None
    data = await _get_json(hass, _route_url(origin, dest, osrm_url))
    return _parse_route(data)
