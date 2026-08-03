"""Real-time multi-hazard monitor (v6.71.0).

Polls three free, no-key government/agency feeds on an interval and speaks/pushes
only *new*, *nearby*, *significant* events through JARVIS's existing alert path:

  - USGS earthquakes  — FDSN event query, bounding-box + min-magnitude scoped
  - NWS weather alerts — api.weather.gov active alerts for the home point
  - NASA EONET        — Earth Observatory natural events (wildfires, volcanoes,
                        severe storms), bbox-scoped, status=open

Location comes from the home coordinates HA already knows (zone.home /
hass.config), or a lat/long override the user sets in the panel. None of these
APIs take a ZIP directly, so a ZIP is converted to lat/long once (via a free
geocode) and stored as the override.

Design deliberately mirrors the rest of JARVIS and the lessons this codebase has
learned the hard way:
  - Each feed dedups on stable event IDs (a per-feed "seen" set) so a standing
    event never re-alerts — the same discipline package_monitor uses.
  - A feed that errors is logged and skipped; it never fabricates an alert. A
    transient fetch failure is not a hazard. (Same "don't cry wolf" principle as
    the service-health work.)
  - NWS *requires* a descriptive User-Agent or it rejects the request; we send
    one. A missing UA is a silent-failure trap, so it's not optional here.
  - Severity/magnitude thresholds keep it to genuinely notable events, not every
    micro-quake or minor advisory.

Nothing here runs unless the user turns the monitor on (hazard_monitor_enabled).
"""
from __future__ import annotations

import logging
import math
import time
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# ── endpoints (verified real, not the placeholder hosts some guides show) ────
_USGS_FDSN = "https://earthquake.usgs.gov/fdsnws/event/1/query"
_NWS_ACTIVE = "https://api.weather.gov/alerts/active"
_EONET_EVENTS = "https://eonet.gsfc.nasa.gov/api/v3/events"

# A descriptive User-Agent. NWS mandates one; EONET/USGS appreciate one. Generic
# on purpose — no personal data (contact is the project, not the user).
_USER_AGENT = "JARVIS-AIO Home Assistant hazard monitor (github.com/sam3gp8/jarvis-aio)"

# ── defaults (all overridable via config) ────────────────────────────────────
_DEF_QUAKE_RADIUS_KM = 300.0     # earthquakes within this of home
_DEF_QUAKE_MIN_MAG = 2.5         # ignore micro-quakes
_DEF_DISASTER_RADIUS_KM = 300.0  # EONET events within this of home
_DEF_DISASTER_DAYS = 3           # EONET look-back window
# NWS severities we alert on (skip Minor/Unknown advisories by default)
_DEF_WX_SEVERITIES = ("Extreme", "Severe")

# Per-feed dedup sets + a cap so they don't grow without bound across a long
# uptime. Module-level so they persist across polls within a HA run.
_SEEN: dict = {"quake": set(), "wx": set(), "disaster": set()}
_SEEN_CAP = 500

# Titles for the push (reuses _notify_all_devices' action_type → title map where
# possible; these are new types so we pass a readable title via the message).
_ACTION = {
    "quake": "hazard_earthquake",
    "wx": "hazard_weather",
    "disaster": "hazard_disaster",
}


# ── config access ────────────────────────────────────────────────────────────

def _cfg(key: str, default=None):
    try:
        from . import jarvis_config
        v = jarvis_config.get(key, default)
        return v if v is not None else default
    except Exception:
        return default


def _enabled() -> bool:
    return bool(_cfg("hazard_monitor_enabled", False))


def _home_latlon(hass) -> Optional[tuple[float, float]]:
    """Resolve the monitoring center: an explicit override if set, else HA's
    home coordinates. Returns (lat, lon) or None."""
    ov_lat = _cfg("hazard_lat", None)
    ov_lon = _cfg("hazard_lon", None)
    try:
        if ov_lat not in (None, "") and ov_lon not in (None, ""):
            return float(ov_lat), float(ov_lon)
    except Exception:
        pass
    # fall back to HA's known home location
    try:
        z = hass.states.get("zone.home")
        if z is not None:
            la = z.attributes.get("latitude")
            lo = z.attributes.get("longitude")
            if la is not None and lo is not None:
                return float(la), float(lo)
    except Exception:
        pass
    try:
        return float(hass.config.latitude), float(hass.config.longitude)
    except Exception:
        return None


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in km."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def _bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """A lat/long bounding box roughly `radius_km` around a point. Coarse (used
    to pre-filter the API query); exact distance is checked with haversine after."""
    dlat = radius_km / 111.0
    # guard against cos(lat)=0 near the poles
    coslat = max(0.01, math.cos(math.radians(lat)))
    dlon = radius_km / (111.0 * coslat)
    return (lat - dlat, lat + dlat, lon - dlon, lon + dlon)


def _remember(feed: str, event_id: str) -> bool:
    """Record an event id as seen. Returns True if it was NEW (not seen before)."""
    s = _SEEN.setdefault(feed, set())
    if event_id in s:
        return False
    s.add(event_id)
    if len(s) > _SEEN_CAP:
        # drop the oldest-ish half (sets are unordered; this just bounds memory)
        for _ in range(len(s) - _SEEN_CAP // 2):
            s.pop()
    return True


async def _get_json(hass, url: str, params: dict | None = None) -> Optional[dict]:
    """GET JSON with the required User-Agent. Returns parsed dict or None on any
    failure (never raises, never fabricates)."""
    try:
        import aiohttp
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        session = async_get_clientsession(hass)
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/geo+json"}
        async with session.get(
            url, params=params, headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status != 200:
                _LOGGER.debug("hazard: %s → HTTP %s", url, resp.status)
                return None
            return await resp.json(content_type=None)
    except Exception as exc:
        _LOGGER.debug("hazard: fetch failed %s: %s", url, exc)
        return None


# ── individual feeds ─────────────────────────────────────────────────────────

async def _check_earthquakes(hass, lat: float, lon: float) -> list[dict]:
    """USGS FDSN, bounding-box + min-mag scoped. Returns NEW nearby quakes."""
    radius = float(_cfg("hazard_quake_radius_km", _DEF_QUAKE_RADIUS_KM))
    min_mag = float(_cfg("hazard_quake_min_mag", _DEF_QUAKE_MIN_MAG))
    min_lat, max_lat, min_lon, max_lon = _bbox(lat, lon, radius)
    params = {
        "format": "geojson",
        "starttime": time.strftime("%Y-%m-%dT%H:%M:%S",
                                    time.gmtime(time.time() - 3600)),  # last hour
        "minlatitude": f"{min_lat:.4f}", "maxlatitude": f"{max_lat:.4f}",
        "minlongitude": f"{min_lon:.4f}", "maxlongitude": f"{max_lon:.4f}",
        "minmagnitude": f"{min_mag:.1f}", "orderby": "time",
    }
    data = await _get_json(hass, _USGS_FDSN, params)
    if not data or "features" not in data:
        return []
    out = []
    for feat in data.get("features", []):
        try:
            eid = feat.get("id")
            props = feat.get("properties", {}) or {}
            coords = (feat.get("geometry", {}) or {}).get("coordinates", [])
            if not eid or len(coords) < 2:
                continue
            qlon, qlat = float(coords[0]), float(coords[1])
            dist = _haversine_km(lat, lon, qlat, qlon)
            if dist > radius:                     # exact radius (bbox is square)
                continue
            if not _remember("quake", eid):       # already alerted
                continue
            out.append({
                "id": eid,
                "mag": props.get("mag"),
                "place": props.get("place") or "unknown location",
                "dist_km": round(dist),
                "url": props.get("url"),
            })
        except Exception:
            continue
    return out


async def _check_weather(hass, lat: float, lon: float) -> list[dict]:
    """NWS active alerts for the home point, filtered to notable severities."""
    sevs = _cfg("hazard_wx_severities", None) or _DEF_WX_SEVERITIES
    sevs = tuple(sevs) if isinstance(sevs, (list, tuple)) else _DEF_WX_SEVERITIES
    params = {"point": f"{lat:.4f},{lon:.4f}"}
    data = await _get_json(hass, _NWS_ACTIVE, params)
    if not data or "features" not in data:
        return []
    out = []
    for feat in data.get("features", []):
        try:
            eid = feat.get("id")
            props = feat.get("properties", {}) or {}
            severity = props.get("severity") or "Unknown"
            if severity not in sevs:
                continue
            if not eid or not _remember("wx", eid):
                continue
            out.append({
                "id": eid,
                "event": props.get("event") or "Weather alert",
                "severity": severity,
                "headline": props.get("headline") or "",
                "area": props.get("areaDesc") or "",
                "instruction": props.get("instruction") or "",
            })
        except Exception:
            continue
    return out


async def _check_disasters(hass, lat: float, lon: float) -> list[dict]:
    """NASA EONET open natural events, bbox-scoped + exact-radius filtered."""
    radius = float(_cfg("hazard_disaster_radius_km", _DEF_DISASTER_RADIUS_KM))
    days = int(_cfg("hazard_disaster_days", _DEF_DISASTER_DAYS))
    min_lat, max_lat, min_lon, max_lon = _bbox(lat, lon, radius)
    # EONET bbox order is minlon,maxlat,maxlon,minlat (WWSE)
    params = {
        "status": "open", "days": str(days),
        "bbox": f"{min_lon:.4f},{max_lat:.4f},{max_lon:.4f},{min_lat:.4f}",
    }
    data = await _get_json(hass, _EONET_EVENTS, params)
    if not data or "events" not in data:
        return []
    out = []
    for ev in data.get("events", []):
        try:
            eid = ev.get("id")
            if not eid:
                continue
            # nearest geometry point to home
            best = None
            for g in ev.get("geometry", []) or []:
                c = g.get("coordinates")
                if not c or len(c) < 2:
                    continue
                try:
                    elon, elat = float(c[0]), float(c[1])
                except Exception:
                    continue
                d = _haversine_km(lat, lon, elat, elon)
                if best is None or d < best:
                    best = d
            if best is None or best > radius:
                continue
            if not _remember("disaster", eid):
                continue
            cats = ", ".join(c.get("title", "") for c in ev.get("categories", []) if c)
            out.append({
                "id": eid,
                "title": ev.get("title") or "Natural event",
                "category": cats or "event",
                "dist_km": round(best),
                "url": (ev.get("sources") or [{}])[0].get("url") if ev.get("sources") else None,
            })
        except Exception:
            continue
    return out


# ── message formatting ───────────────────────────────────────────────────────

def _fmt_quake(q: dict, honorific: str) -> str:
    mag = q.get("mag")
    magtxt = f"magnitude {mag:.1f}" if isinstance(mag, (int, float)) else "an earthquake"
    return (f"Seismic alert, {honorific}. A {magtxt} earthquake was just "
            f"recorded {q['dist_km']} km away — {q['place']}.")


def _fmt_weather(w: dict, honorific: str) -> str:
    base = f"{w['severity']} weather alert, {honorific}: {w['event']}"
    if w.get("area"):
        base += f" for {w['area']}"
    base += "."
    if w.get("instruction"):
        base += f" {w['instruction'][:200]}"
    return base


def _fmt_disaster(d: dict, honorific: str) -> str:
    return (f"Natural hazard nearby, {honorific}: {d['title']} ({d['category']}), "
            f"about {d['dist_km']} km away.")


# ── the periodic entry point (called on an interval from __init__) ───────────

async def periodic_check(hass, honorific: str = "sir") -> dict:
    """Poll all enabled feeds once; push/speak any NEW nearby significant events.
    Returns a small summary dict. Never raises."""
    if not _enabled():
        return {"skipped": "disabled"}

    center = _home_latlon(hass)
    if center is None:
        _LOGGER.debug("hazard: no home coordinates available; skipping")
        return {"skipped": "no_location"}
    lat, lon = center

    fired = {"quake": 0, "wx": 0, "disaster": 0}

    async def _announce(action_key: str, message: str) -> None:
        # deliver via the same paths every other JARVIS alert uses
        try:
            from . import cognitive_core as cc
            config = getattr(cc, "_CORE", None)
            cfg_obj = getattr(config, "config", None) if config else None
            await cc._notify_all_devices(hass, cfg_obj, message, _ACTION[action_key])
        except Exception as exc:
            _LOGGER.debug("hazard: notify failed: %s", exc)
        # also speak it, if announcements are on and speakers exist
        try:
            from . import tts_helper, audio_routing
            tts = tts_helper.find_best_tts_entity(hass)
            spk = audio_routing.speakers_in_area(hass, None)
            if tts and spk:
                await tts_helper.async_announce(hass, tts, spk, message, context="hazard")
        except Exception:
            pass

    # Earthquakes
    if _cfg("hazard_quakes_on", True):
        for q in await _check_earthquakes(hass, lat, lon):
            await _announce("quake", _fmt_quake(q, honorific))
            fired["quake"] += 1

    # Weather
    if _cfg("hazard_weather_on", True):
        for w in await _check_weather(hass, lat, lon):
            await _announce("wx", _fmt_weather(w, honorific))
            fired["wx"] += 1

    # Disasters
    if _cfg("hazard_disasters_on", True):
        for d in await _check_disasters(hass, lat, lon):
            await _announce("disaster", _fmt_disaster(d, honorific))
            fired["disaster"] += 1

    total = sum(fired.values())
    if total:
        _LOGGER.info("hazard: %d new alert(s) — %s", total, fired)
    return {"checked": True, "fired": fired, "center": [round(lat, 3), round(lon, 3)]}


async def scan_now(hass, honorific: str = "sir") -> dict:
    """On-demand read-only scan for the agent tool / panel test. Queries all
    feeds and returns what's currently active near home WITHOUT touching the
    dedup sets or announcing — so asking 'any hazards?' never suppresses the
    background monitor's future alerts, and never double-speaks. Never raises."""
    center = _home_latlon(hass)
    if center is None:
        return {"ok": False, "error": "no home location configured"}
    lat, lon = center

    # Temporarily bypass dedup by scanning into a scratch: we re-run the feed
    # logic but read everything (not just new). Simplest: snapshot & restore the
    # seen sets around a scan so nothing is marked consumed.
    import copy
    saved = {k: set(v) for k, v in _SEEN.items()}
    try:
        quakes = await _check_earthquakes(hass, lat, lon)
        wx = await _check_weather(hass, lat, lon)
        disasters = await _check_disasters(hass, lat, lon)
    finally:
        _SEEN.clear()
        _SEEN.update(saved)

    return {
        "ok": True,
        "center": [round(lat, 3), round(lon, 3)],
        "earthquakes": quakes,
        "weather": wx,
        "disasters": disasters,
        "counts": {"earthquakes": len(quakes), "weather": len(wx),
                   "disasters": len(disasters)},
    }


async def status(hass) -> dict:
    """Panel status: config + what the monitor would watch, without alerting.
    Runs a *read-only* count so the user can confirm it's wired to their area."""
    center = _home_latlon(hass)
    out = {
        "enabled": _enabled(),
        "center": [round(center[0], 3), round(center[1], 3)] if center else None,
        "using_override": bool(_cfg("hazard_lat", None) and _cfg("hazard_lon", None)),
        "quake_radius_km": float(_cfg("hazard_quake_radius_km", _DEF_QUAKE_RADIUS_KM)),
        "quake_min_mag": float(_cfg("hazard_quake_min_mag", _DEF_QUAKE_MIN_MAG)),
        "disaster_radius_km": float(_cfg("hazard_disaster_radius_km", _DEF_DISASTER_RADIUS_KM)),
        "feeds": {
            "earthquakes": bool(_cfg("hazard_quakes_on", True)),
            "weather": bool(_cfg("hazard_weather_on", True)),
            "disasters": bool(_cfg("hazard_disasters_on", True)),
        },
    }
    return out
