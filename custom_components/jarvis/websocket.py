"""
JARVIS Panel WebSocket API (v5.4.2).

Registers the `jarvis/get_panel_data` WebSocket command that the custom
panel calls (on mount + every 5s) to refresh live state.

The single command returns everything the panel needs in one round-trip:
status flags, area registry with capabilities and occupancy, dominant
room, satellite count, bedroom count, uptime.

Activity log is a separate endpoint (deferred to session 3, needs DB work).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from . import audio_routing, sleep_detection
from .const import (
    CONF_BEDROOM_AREAS,
    CONF_BROADCAST_GROUP,
    CONF_GEMINI_API_KEY,
    CONF_NOTIFY_SERVICE,
    CONF_OBSERVER_ENABLED,
    CONF_OBSERVER_QUIET_END,
    CONF_OBSERVER_QUIET_START,
    DEFAULT_OBSERVER_QUIET_END,
    DEFAULT_OBSERVER_QUIET_START,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Startup wall-clock — for uptime computation
_STARTUP_TS: float = time.time()


# ─── Command registration ────────────────────────────────────────────────────

@callback
def async_register(hass: HomeAssistant) -> None:
    """Register all JARVIS panel WebSocket commands. Idempotent-ish."""
    try:
        websocket_api.async_register_command(hass, ws_get_panel_data)
        websocket_api.async_register_command(hass, ws_get_activity_log)
        websocket_api.async_register_command(hass, ws_update_config)
        websocket_api.async_register_command(hass, ws_set_lockdown)
        websocket_api.async_register_command(hass, ws_get_knowledge)
        websocket_api.async_register_command(hass, ws_add_knowledge)
        websocket_api.async_register_command(hass, ws_forget_knowledge)
        websocket_api.async_register_command(hass, ws_root_cause)
        websocket_api.async_register_command(hass, ws_reload_appliances)
        websocket_api.async_register_command(hass, ws_search_memory)
        websocket_api.async_register_command(hass, ws_get_debug_log)
        websocket_api.async_register_command(hass, ws_get_cognitive_status)
        websocket_api.async_register_command(hass, ws_list_models)
        websocket_api.async_register_command(hass, ws_suggestion_action)
        websocket_api.async_register_command(hass, ws_goal_action)
        websocket_api.async_register_command(hass, ws_get_person_routines)
        websocket_api.async_register_command(hass, ws_get_area_sparklines)
        websocket_api.async_register_command(hass, ws_camera_snapshot)
        websocket_api.async_register_command(hass, ws_camera_diagnostics)
        websocket_api.async_register_command(hass, ws_rename_camera)
        websocket_api.async_register_command(hass, ws_camera_location)
        websocket_api.async_register_command(hass, ws_mmwave_overview)
        websocket_api.async_register_command(hass, ws_documents)
        websocket_api.async_register_command(hass, ws_vector_backend)
    except Exception as exc:
        _LOGGER.debug("WS command register note: %s", exc)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _get_entry(hass: HomeAssistant):
    """Return the first JARVIS config entry's ConfigEntry object, or None."""
    # hass.data[DOMAIN] is keyed by entry_id, values are dicts of runtime state.
    # We need the actual ConfigEntry object for options/data lookups.
    for entry in hass.config_entries.async_entries(DOMAIN):
        return entry
    return None


def _entry_opt(entry, key: str, default=None):
    """Read from options, then data, then default."""
    if entry is None:
        return default
    return entry.options.get(key, entry.data.get(key, default))


def _runtime_opt(hass: HomeAssistant, entry, key: str, default=None):
    """Read from runtime_config (panel toggles), then options, then data."""
    if entry is not None:
        data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        rc = data.get("runtime_config", {})
        if key in rc:
            return rc[key]
    return _entry_opt(entry, key, default)


def _area_name(hass: HomeAssistant, area_id: str) -> str:
    """Friendly name for an area_id."""
    try:
        from homeassistant.helpers import area_registry as ar
        reg = ar.async_get(hass)
        area = reg.async_get_area(area_id)
        if area:
            return area.name or area_id
    except Exception:
        pass
    return area_id


def _entities_in_area(hass: HomeAssistant, area_id: str) -> list[str]:
    """All entity_ids whose (entity area) or (device area) matches."""
    from homeassistant.helpers import entity_registry as er, device_registry as dr
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    out = []
    for ent in ent_reg.entities.values():
        ent_area = ent.area_id
        if not ent_area and ent.device_id:
            dev = dev_reg.async_get(ent.device_id)
            if dev:
                ent_area = dev.area_id
        if ent_area == area_id:
            out.append(ent.entity_id)
    return out


def _area_capabilities(hass: HomeAssistant, area_id: str) -> list[str]:
    """
    Return a sorted list of capability codes present in this area.
    Each code is what the panel will render as icon + label.
    """
    caps: set[str] = set()
    for eid in _entities_in_area(hass, area_id):
        domain = eid.split(".", 1)[0]
        state = hass.states.get(eid)
        dclass = state.attributes.get("device_class") if state else None

        if domain == "assist_satellite":
            caps.add("sat")
        elif domain == "media_player":
            caps.add("spkr")
        elif domain == "camera":
            caps.add("cam")
        elif domain == "binary_sensor":
            if dclass in ("occupancy", "motion", "presence"):
                caps.add("mmwave")
            elif dclass in ("door", "window", "garage_door", "opening"):
                caps.add("door")
            elif dclass in ("moisture",):
                caps.add("leak")
            elif dclass in ("smoke", "gas", "carbon_monoxide"):
                caps.add("alarm")
            elif dclass in ("safety", "tamper", "problem"):
                caps.add("alarm")
        elif domain == "light":
            caps.add("light")
        elif domain == "switch":
            caps.add("switch")
        elif domain == "lock":
            caps.add("lock")
        elif domain == "climate":
            caps.add("climate")

    # Ordering: sat, spkr, mmwave, cam, light, switch, lock, climate, door, leak, alarm
    order = ["sat", "spkr", "mmwave", "cam", "light", "switch", "lock", "climate", "door", "leak", "alarm"]
    return [c for c in order if c in caps]


def _is_outdoor_area(hass: HomeAssistant, area_id: str) -> bool:
    """Heuristic: does the area name look outdoor?"""
    name = (_area_name(hass, area_id) or "").lower()
    outdoor_keywords = (
        "yard", "garden", "driveway", "patio", "deck", "porch",
        "pool", "outdoor", "outside", "exterior", "lawn",
    )
    return any(kw in name for kw in outdoor_keywords)


def _dominant_area(hass: HomeAssistant) -> str | None:
    """
    Pick the 'most alive' area — currently occupied, with most-recent motion.
    Prefers indoor areas over outdoor ones (you don't live in the yard).
    Returns area_id or None.
    """
    occupied = audio_routing.currently_occupied_areas(hass)
    if not occupied:
        return None

    # Split into indoor vs outdoor
    indoor = [a for a in occupied if not _is_outdoor_area(hass, a)]
    outdoor = [a for a in occupied if _is_outdoor_area(hass, a)]
    # Strongly prefer indoor; only use outdoor if that's all we have
    candidates = indoor or outdoor

    # Rank by most recent occupancy sensor change
    best_area = None
    best_ts = 0.0
    for area_id in candidates:
        for eid in audio_routing.presence_entities_in_area(hass, area_id):
            state = hass.states.get(eid)
            if state is None:
                continue
            # last_changed is a datetime
            try:
                ts = state.last_changed.timestamp()
            except Exception:
                continue
            if ts > best_ts:
                best_ts = ts
                best_area = area_id

    return best_area or candidates[0]


def _area_light_state(hass: HomeAssistant, area_id: str) -> tuple[int, int]:
    """Count (lights_on, lights_total) for an area — cheap, light-domain only.
    Used to drive the per-room light indicator + toggle in the 3D house."""
    on = total = 0
    for eid in _entities_in_area(hass, area_id):
        if not eid.startswith("light."):
            continue
        st = hass.states.get(eid)
        if st is None:
            continue
        total += 1
        if st.state == "on":
            on += 1
    return on, total


def _area_temp_humidity_entities(hass: HomeAssistant, area_id: str) -> tuple[Optional[str], Optional[str]]:
    """The first temperature/humidity sensor entity_id found in an area, or
    None. Same resolution order _area_live_readings uses, factored out so
    the areas grid and the sparkline history fetch use one source of truth."""
    temp_eid = None
    humidity_eid = None
    for eid in _entities_in_area(hass, area_id):
        if temp_eid and humidity_eid:
            break
        state = hass.states.get(eid)
        if state is None or eid.split(".", 1)[0] != "sensor":
            continue
        dclass = state.attributes.get("device_class")
        if dclass == "temperature" and temp_eid is None:
            temp_eid = eid
        elif dclass == "humidity" and humidity_eid is None:
            humidity_eid = eid
    return temp_eid, humidity_eid


def _area_live_readings(hass: HomeAssistant, area_id: str) -> dict:
    """Pull temperature, humidity, any lights-on count in the area."""
    temp = None
    humidity = None
    lights_on = 0
    lights_total = 0
    last_motion_seconds = None

    for eid in _entities_in_area(hass, area_id):
        state = hass.states.get(eid)
        if state is None:
            continue
        domain = eid.split(".", 1)[0]
        dclass = state.attributes.get("device_class")

        if domain == "sensor":
            if dclass == "temperature" and temp is None:
                try:
                    val = float(state.state)
                    unit = state.attributes.get("unit_of_measurement", "")
                    temp = f"{int(round(val))}°{unit.replace('°', '')[:1] or 'F'}"
                except (ValueError, TypeError):
                    pass
            elif dclass == "humidity" and humidity is None:
                try:
                    humidity = f"{int(round(float(state.state)))}%"
                except (ValueError, TypeError):
                    pass
        elif domain == "light":
            lights_total += 1
            if state.state == "on":
                lights_on += 1
        elif domain == "binary_sensor" and dclass in ("occupancy", "motion", "presence"):
            try:
                age = (time.time() - state.last_changed.timestamp())
                if last_motion_seconds is None or age < last_motion_seconds:
                    last_motion_seconds = age
            except Exception:
                pass

    lights_display = None
    if lights_total > 0:
        lights_display = f"{lights_on}/{lights_total}" if lights_total > 1 else ("ON" if lights_on else "OFF")

    return {
        "temp": temp,
        "humidity": humidity,
        "lights": lights_display,
        "last_motion_seconds": last_motion_seconds,
    }


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    return f"{int(seconds // 3600)}h"


def _satellite_count(hass: HomeAssistant) -> tuple[int, int]:
    """Return (available, total) satellite count."""
    total = 0
    avail = 0
    for state in hass.states.async_all("assist_satellite"):
        total += 1
        if state.state not in ("unavailable", "unknown"):
            avail += 1
    return avail, total


def _get_satellites(hass: HomeAssistant) -> list[dict]:
    """Return list of satellites with entity_id, name, and area."""
    satellites = []
    try:
        from homeassistant.helpers import (
            entity_registry as er,
            device_registry as dr,
            area_registry as areg,
        )
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)
        area_reg = areg.async_get(hass)

        for state in hass.states.async_all("assist_satellite"):
            entry = ent_reg.async_get(state.entity_id)
            area_name = ""
            if entry and entry.device_id:
                device = dev_reg.async_get(entry.device_id)
                if device and device.area_id:
                    area = area_reg.async_get_area(device.area_id)
                    area_name = area.name if area else device.area_id
            name = state.attributes.get("friendly_name", state.entity_id)
            satellites.append({
                "entity_id": state.entity_id,
                "name": name,
                "area": area_name,
            })
    except Exception:
        for state in hass.states.async_all("assist_satellite"):
            satellites.append({
                "entity_id": state.entity_id,
                "name": state.attributes.get("friendly_name", state.entity_id),
                "area": "",
            })
    return satellites


def _get_camera_overrides() -> dict:
    """The camera_overrides runtime map (original → frame source), for the
    panel to mirror server-side source resolution (v6.47.0). Never raises."""
    try:
        from . import jarvis_config
        ov = jarvis_config.get("camera_overrides", {}) or {}
        return {str(k): str(v) for k, v in ov.items()} if isinstance(ov, dict) else {}
    except Exception:
        return {}


def _get_camera_names() -> dict:
    """The camera_names runtime map (entity_id → JARVIS-only display name),
    v6.48.0. Never raises."""
    try:
        from . import jarvis_config
        nm = jarvis_config.get("camera_names", {}) or {}
        return {str(k): str(v) for k, v in nm.items()} if isinstance(nm, dict) else {}
    except Exception:
        return {}


def _get_cameras(hass: HomeAssistant) -> list[dict]:
    """Camera entities for the picker/chips. `name` honours the JARVIS-only
    camera_names map (v6.48.0); `raw_name` keeps the HA friendly name so the
    rename UI can show what blank reverts to."""
    cams = []
    names = _get_camera_names()
    try:
        from . import outdoor
        from .camera import display_name
        indoor_list = outdoor._cfg_list("indoor_entities")
        outdoor_list = outdoor._cfg_list("outdoor_entities")
        for state in hass.states.async_all("camera"):
            friendly = state.attributes.get("friendly_name", state.entity_id)
            cams.append({
                "entity_id": state.entity_id,
                "name": display_name(state.entity_id, friendly, names),
                "raw_name": friendly,
                # v6.49.0: location designation for the whole cognitive stack
                # (intrusion filter, notable-events, motion scan all consult
                # outdoor.is_outdoor).
                "outdoor": outdoor.is_outdoor(hass, state.entity_id, friendly),
                "location_mode": outdoor.location_mode(
                    state.entity_id, indoor_list, outdoor_list),
            })
    except Exception:
        pass
    return sorted(cams, key=lambda c: c["name"])


def _get_cast_devices(hass: HomeAssistant) -> list[dict]:
    """Return list of Cast/Google media_player entities."""
    devices = []
    for state in hass.states.async_all("media_player"):
        # Include cast, Google, Sonos, Lenovo, and group players
        eid = state.entity_id
        name = state.attributes.get("friendly_name", eid)
        platform = state.attributes.get("platform", "")
        # Cast devices typically have these attributes
        is_cast = (
            "cast" in platform.lower()
            or "google" in name.lower()
            or "nest" in name.lower()
            or "lenovo" in name.lower()
            or "sonos" in name.lower()
            or "home_group" in eid
            or "group" in eid
            or state.attributes.get("supported_features", 0) & 16384  # PLAY_MEDIA
        )
        if is_cast and state.state not in ("unavailable",):
            devices.append({
                "entity_id": eid,
                "name": name,
            })
    return devices


def _all_areas_with_anything(hass: HomeAssistant) -> list[str]:
    """Areas that have at least one satellite, speaker, or presence sensor."""
    try:
        from homeassistant.helpers import area_registry as ar
        reg = ar.async_get(hass)
        all_ids = [a.id for a in reg.async_list_areas()]
    except Exception:
        return []

    interesting = []
    for aid in all_ids:
        if _area_capabilities(hass, aid):
            interesting.append(aid)
    return interesting


# ─── WebSocket command ───────────────────────────────────────────────────────

@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/get_panel_data",
})
@websocket_api.async_response
async def ws_get_panel_data(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return all data the panel needs for one render."""
    try:
        entry = _get_entry(hass)

        # ── Status flags ────────────────────────────────────────────────────
        observer_running = False
        if entry is not None:
            data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            observer_running = bool(data.get("observer_running", False))

        bedroom_areas = _entry_opt(entry, CONF_BEDROOM_AREAS, []) or []
        quiet_start = _entry_opt(entry, CONF_OBSERVER_QUIET_START, DEFAULT_OBSERVER_QUIET_START)
        quiet_end = _entry_opt(entry, CONF_OBSERVER_QUIET_END, DEFAULT_OBSERVER_QUIET_END)

        sleeping, sleep_reason = sleep_detection.is_sleeping(
            hass,
            bedroom_area_ids=bedroom_areas,
            quiet_start=quiet_start,
            quiet_end=quiet_end,
        )

        gemini_key = bool(_entry_opt(entry, CONF_GEMINI_API_KEY, ""))
        broadcast_group = _entry_opt(entry, CONF_BROADCAST_GROUP, "") or ""
        notify_service = _entry_opt(entry, CONF_NOTIFY_SERVICE, "") or ""
        observer_enabled_cfg = bool(_runtime_opt(hass, entry, CONF_OBSERVER_ENABLED, False))

        sat_avail, sat_total = _satellite_count(hass)

        # ── Areas grid ──────────────────────────────────────────────────────
        areas_list = []
        for aid in _all_areas_with_anything(hass):
            caps = _area_capabilities(hass, aid)
            active = audio_routing.is_area_occupied(hass, aid)
            l_on, l_total = _area_light_state(hass, aid)
            readings = _area_live_readings(hass, aid)
            temp_eid, humidity_eid = _area_temp_humidity_entities(hass, aid)
            areas_list.append({
                "id":       aid,
                "name":     _area_name(hass, aid),
                "caps":     caps,
                "active":   active,
                "bedroom":  aid in bedroom_areas,
                "lights_on":    l_on,
                "lights_total": l_total,
                "temp":         readings.get("temp"),
                "humidity":     readings.get("humidity"),
                "temp_entity":     temp_eid,
                "humidity_entity": humidity_eid,
                "last_motion":  _format_duration(readings.get("last_motion_seconds")),
            })
        # Sort: active first, then bedrooms, then alphabetical
        areas_list.sort(key=lambda a: (not a["active"], not a["bedroom"], a["name"].lower()))

        # ── Dominant room ───────────────────────────────────────────────────
        dominant_id = _dominant_area(hass)
        if dominant_id:
            readings = _area_live_readings(hass, dominant_id)
            dominant_satellites = audio_routing.satellites_in_area(hass, dominant_id)
            sat_id = dominant_satellites[0] if dominant_satellites else None
            dominant = {
                "area_id":    dominant_id,
                "name":       _area_name(hass, dominant_id),
                "subtitle":   f"Occupied · {_format_duration(readings.get('last_motion_seconds'))}" if readings.get('last_motion_seconds') is not None else "Occupied",
                "coord":      f"#{dominant_id[:8]}",
                "temp":       readings.get("temp") or "—",
                "humidity":   readings.get("humidity") or "—",
                "lights":     readings.get("lights") or "—",
                "satellite":  sat_id.split(".", 1)[-1][:20] if sat_id else "—",
                "last_motion": _format_duration(readings.get("last_motion_seconds")),
            }
        else:
            # No presence detected anywhere
            anyone = audio_routing.anyone_home(hass)
            dominant = {
                "area_id":    None,
                "name":       "AWAY" if not anyone else "AT HOME",
                "subtitle":   "no presence detected",
                "coord":      "—",
                "temp":       "—",
                "humidity":   "—",
                "lights":     "—",
                "satellite":  "—",
                "last_motion": "—",
            }

        # ── Status tiles ────────────────────────────────────────────────────
        status = {
            "observer": {
                "state": "RUNNING" if observer_running else ("READY" if observer_enabled_cfg else "DISABLED"),
                "level": "live" if observer_running else ("warn" if observer_enabled_cfg else "off"),
            },
            "sleep": {
                "state": "ASLEEP" if sleeping else "AWAKE",
                "level": "warn" if sleeping else "live",
            },
            "gemini": {
                "state": "READY" if gemini_key else "UNSET",
                "level": "live" if gemini_key else "warn",
            },
            "broadcast": {
                "state": "ONLINE" if broadcast_group else "UNSET",
                "level": "live" if broadcast_group else "warn",
            },
            "notify": {
                "state": "READY" if notify_service else "UNSET",
                "level": "live" if notify_service else "warn",
            },
            "satellites": {
                "state": f"{sat_avail} / {sat_total}" if sat_total > 0 else "NONE",
                "level": "live" if sat_avail == sat_total and sat_total > 0 else ("warn" if sat_total > 0 else "off"),
            },
        }

        uptime_seconds = time.time() - _STARTUP_TS
        uptime_str = _format_uptime(uptime_seconds)

        # ── Config flags for settings panel ─────────────────────────────
        announcements_on = bool(_runtime_opt(hass, entry, "announcements_enabled", False))
        sentinel_on = bool(_runtime_opt(hass, entry, "sentinel_enabled", True))

        # Available notify services for phone notification dropdown
        notify_services = []
        try:
            for svc in hass.services.async_services().get("notify", {}):
                notify_services.append(f"notify.{svc}")
        except Exception:
            pass
        current_notify = str(_runtime_opt(hass, entry, CONF_NOTIFY_SERVICE, "") or "")

        result = {
            "status":         status,
            "version":        _INTEGRATION_VERSION,
            "meta": {
                "bedrooms":          len(bedroom_areas),
                "areas_monitored":   len(areas_list),
                "announcements_today": _get_announcements_today(),
                "est_cost":          "—",
                "uptime":            uptime_str,
            },
            "dominant":       dominant,
            "areas":          areas_list,
            "sleep_reason":   sleep_reason if sleeping else None,
            "doorbell_training": _get_doorbell_training(),
            "doors":          _get_door_states(hass),
            "lockdown":       _get_lockdown_status(),
            "intrusion":      _get_intrusion_status(),
            "knowledge":      _get_knowledge_stats(),
            "suggestions":    _get_suggestions(),
            "goals":          _get_goals(),
            "config": {
                "announcements_enabled": announcements_on,
                "sentinel_enabled": sentinel_on,
                "observer_enabled": observer_enabled_cfg,
                "cognition_enabled": bool(_runtime_opt(hass, entry, "cognition_enabled", True)),
                "camera_auto_analyze": bool(_runtime_opt(hass, entry, "camera_auto_analyze", True)),
                "camera_auto_analyze_motion": bool(_runtime_opt(hass, entry, "camera_auto_analyze_motion", False)),
                "package_detection": bool(_runtime_opt(hass, entry, "package_detection", True)),
                "visitor_learning": bool(_runtime_opt(hass, entry, "visitor_learning", True)),
                "rich_reasoning": bool(_runtime_opt(hass, entry, "rich_reasoning", False)),
                "light_control_enabled": bool(_runtime_opt(hass, entry, "light_control_enabled", True)),
                "appliance_power_guessing": bool(_runtime_opt(hass, entry, "appliance_power_guessing", False)),
                "llm_base_url": str(_runtime_opt(hass, entry, "llm_base_url", "") or ""),
                "notify_service": current_notify,
                "notify_services_available": notify_services,
                "sentinel_rules": _get_sentinel_rules(),
                "disabled_sentinel_rules": _get_disabled_rules(hass, entry),
                "observer_stats": _get_observer_stats(),
                "lockdown": _get_lockdown_status(),
                "appliances": _get_appliance_status(),
                "appliance_profile": _get_runtime_json(hass, entry, "appliance_profile", []),
                "appliance_announce_unknown": _runtime_opt(hass, entry, "appliance_announce_unknown", False),
                "memory_stats": _get_memory_stats(),
                "satellites": _get_satellites(hass),
                "cast_devices": _get_cast_devices(hass),
                "cameras": _get_cameras(hass),
                "camera_overrides": _get_camera_overrides(),
                "camera_names": _get_camera_names(),
                "satellite_pairings": _get_runtime_json(hass, entry, "satellite_pairings", {}),
                "announcement_speakers": _get_runtime_json(hass, entry, "announcement_speakers", []),
                "floor_plan_rooms": _get_runtime_json(hass, entry, "floor_plan_rooms", {}),
                "floor_plan_bg": _get_runtime_json(hass, entry, "floor_plan_bg", {}),
                "door_mapping": _get_runtime_json(hass, entry, "door_mapping", {}),
                "floor_plan_address": _get_runtime_str(hass, entry, "floor_plan_address", ""),
                # AI model selection (provider + model per role) — for the
                # Settings "AI Models" section's live-fetched dropdowns.
                "llm_provider":        str(_runtime_opt(hass, entry, "llm_provider", "groq") or "groq"),
                "model":               str(_runtime_opt(hass, entry, "model", "") or ""),
                "classifier_provider": str(_runtime_opt(hass, entry, "classifier_provider", "groq") or "groq"),
                "classifier_model":    str(_runtime_opt(hass, entry, "classifier_model", "") or ""),
                "reasoning_provider":  str(_runtime_opt(hass, entry, "reasoning_provider", "groq") or "groq"),
                "reasoning_model":     str(_runtime_opt(hass, entry, "reasoning_model", "") or ""),
                "review_provider":     str(_runtime_opt(hass, entry, "review_provider", "groq") or "groq"),
                "review_model":        str(_runtime_opt(hass, entry, "review_model", "") or ""),
                "vision_provider":     str(_runtime_opt(hass, entry, "vision_provider", "groq") or "groq"),
                "vision_model":        str(_runtime_opt(hass, entry, "vision_model", "") or ""),
                "camera_reasoning_provider": str(_runtime_opt(hass, entry, "camera_reasoning_provider", "groq") or "groq"),
                "camera_reasoning_model":    str(_runtime_opt(hass, entry, "camera_reasoning_model", "") or ""),
            },
        }
        connection.send_result(msg["id"], result)
    except Exception as exc:
        _LOGGER.exception("ws_get_panel_data failed: %s", exc)
        connection.send_error(msg["id"], "panel_data_failed", str(exc))


def _format_uptime(seconds: float) -> str:
    """'2d 14h' / '14h 22m' / '42m 10s' format."""
    seconds = int(seconds)
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    if d > 0:
        return f"{d}d {h}h"
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _get_announcements_today() -> int:
    """Get today's spoken announcement count from DB. Returns 0 on error."""
    try:
        from .database import get_activity_count_today
        return get_activity_count_today()
    except Exception:
        return 0


def _door_entity_open(state_obj) -> bool:
    """Back-compat shim — door open logic now lives in door_state.py."""
    from . import door_state
    return door_state.entity_is_open(state_obj)


def _get_door_states(hass: HomeAssistant) -> dict:
    """
    Open/closed state of the home's doors for the Residence 3D model. Reads the
    explicit ``door_mapping`` (slot -> entity_id) the user set on the Residence
    tab, then delegates to door_state.get_door_states which honours it and
    auto-detects the rest. Never raises.
    """
    try:
        from . import door_state
        entry = _get_entry(hass)
        mapping = _get_runtime_json(hass, entry, "door_mapping", {}) or {}
        return door_state.get_door_states(hass, mapping)
    except Exception:
        return {}


def _get_doorbell_training() -> dict:
    """Doorbell training-dataset stats + the most recent analysed events, for
    the panel's Doorbell Training view. Never raises."""
    try:
        from . import doorbell_training
        return {
            "stats": doorbell_training.stats(),
            "recent": doorbell_training.load_events(limit=12),
        }
    except Exception:
        return {"stats": {"total": 0}, "recent": []}


def _get_suggestions() -> list[dict]:
    """Pending automation suggestions from the pattern engine, panel-shaped.
    Never raises."""
    try:
        from .pattern_analyzer import get_analyzer
        out = []
        for s in get_analyzer().get_pending_suggestions():
            out.append({
                "id": s.get("id"),
                "created": s.get("created", ""),
                "description": s.get("description", ""),
                "yaml": s.get("automation_yaml", ""),
                "confidence": round(float(s.get("confidence", 0) or 0), 2),
                "count": s.get("pattern_count", 0),
            })
        return out
    except Exception:
        return []


def _get_goals() -> list[dict]:
    """Active + recently closed goals, panel-shaped. Never raises."""
    try:
        from . import goals
        out = []
        for g in goals.recent(limit=20):
            steps = g.get("steps") or []
            done = sum(1 for s in steps if s.get("status") == "done")
            out.append({
                "id": g.get("id"),
                "title": g.get("title", ""),
                "outcome": g.get("outcome", ""),
                "status": g.get("status", "active"),
                "steps_done": done,
                "steps_total": len(steps),
                "steps": steps,
                "next_check_ts": g.get("next_check_ts", ""),
                "deadline_ts": g.get("deadline_ts"),
                "last_result": g.get("last_result", ""),
                "updated_ts": g.get("updated_ts", ""),
            })
        return out
    except Exception:
        return []


def _get_person_routines() -> dict:
    """Per-person learned routines from the pattern engine, grouped by
    person for the Memory panel. Never raises."""
    try:
        from .pattern_analyzer import get_analyzer
        rows = get_analyzer().get_person_patterns()
        grouped: dict[str, list[dict]] = {}
        for r in rows:
            person = r.get("person", "")
            if not person:
                continue
            grouped.setdefault(person, []).append({
                "id": r.get("id"),
                "pattern_type": r.get("pattern_type", ""),
                "description": r.get("description", ""),
                "confidence": round(float(r.get("confidence", 0) or 0), 2),
                "occurrences": r.get("occurrences", 0),
                "last_seen": r.get("last_seen", ""),
            })
        return grouped
    except Exception:
        return {}


def _downsample(vals: list[float], n: int) -> list[float]:
    """Evenly-spaced downsample to at most n points — a sparkline doesn't
    need every recorder sample, just the shape."""
    if len(vals) <= n or n <= 0:
        return vals
    step = len(vals) / n
    return [vals[int(i * step)] for i in range(n)]


async def _get_area_sparklines(hass: HomeAssistant, entity_map: dict[str, dict[str, Optional[str]]],
                                hours: float = 12.0, points: int = 20) -> dict:
    """Compact recent history for area-tile sparklines, keyed by area_id:
    {area_id: {"temp": [floats], "humidity": [floats]}}. entity_map is
    {area_id: {"temp": entity_id_or_None, "humidity": entity_id_or_None}}.

    v6.43.0 — the first use of HA's recorder in this integration. Pattern
    learning deliberately built its own telemetry (patterns.db) instead of
    depending on recorder, but that store explicitly excludes sensor/
    binary_sensor domains as noise — exactly the domains a temperature
    sparkline needs. Recorder is the right tool for this one job. Read-only,
    wrapped defensively throughout: recorder internals vary by HA version
    and this integration has no other code path exercising them.
    Never raises — an empty dict just means no sparklines this cycle.
    """
    entity_ids = sorted({eid for m in entity_map.values() for eid in m.values() if eid})
    if not entity_ids:
        return {}
    try:
        from datetime import timedelta
        from homeassistant.components.recorder import get_instance, history
        from homeassistant.util import dt as dt_util
    except Exception:
        return {}

    end = dt_util.utcnow()
    start = end - timedelta(hours=hours)

    def _fetch() -> dict:
        return history.get_significant_states(
            hass, start, end, entity_ids,
            minimal_response=True, no_attributes=True)

    try:
        raw = await get_instance(hass).async_add_executor_job(_fetch)
    except Exception as exc:
        _LOGGER.debug("sparkline history fetch failed: %s", exc)
        return {}
    if not raw:
        return {}

    def _series(eid: str) -> list[float]:
        vals: list[float] = []
        for s in (raw.get(eid) or []):
            # minimal_response mixes full State objects (first/last entry)
            # with plain {"state": ..., "last_changed": ...} dicts.
            raw_state = getattr(s, "state", None) if not isinstance(s, dict) else s.get("state")
            try:
                vals.append(float(raw_state))
            except (TypeError, ValueError):
                continue
        return _downsample(vals, points)

    out: dict = {}
    for area_id, m in entity_map.items():
        entry: dict = {}
        if m.get("temp"):
            v = _series(m["temp"])
            if v:
                entry["temp"] = v
        if m.get("humidity"):
            v = _series(m["humidity"])
            if v:
                entry["humidity"] = v
        if entry:
            out[area_id] = entry
    return out


def _get_sentinel_rules() -> list[dict]:
    """Return list of sentinel rule IDs and descriptions."""
    try:
        from .sentinel import DEFAULT_RULES
        return [{"id": r["id"], "desc": r.get("message", "")[:60]} for r in DEFAULT_RULES]
    except Exception:
        return []


def _get_disabled_rules(hass: HomeAssistant, entry) -> list[str]:
    """Return list of disabled sentinel rule IDs from runtime config."""
    if entry is None:
        return []
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    rc = data.get("runtime_config", {}) if isinstance(data, dict) else {}
    raw = rc.get("disabled_sentinel_rules", _entry_opt(entry, "disabled_sentinel_rules", "[]"))
    if isinstance(raw, list):
        return raw
    try:
        import json
        return json.loads(raw) if isinstance(raw, str) else []
    except Exception:
        return []


def _get_lockdown_status() -> dict:
    """Formal lockdown state for the panel."""
    try:
        from . import cognitive_core
        return cognitive_core.lockdown_status()
    except Exception:
        return {"active": False, "since": 0.0, "reason": "", "auto": False, "exempt_windows": 0}


def _get_intrusion_status() -> dict:
    """Active intrusion investigation (breach point + route) for the panel."""
    try:
        from . import cognitive_core
        return cognitive_core.intrusion_status()
    except Exception:
        return {"active": False, "confirmed": False}


def _get_knowledge_stats() -> dict:
    """Curated-knowledge summary (counts) for the panel."""
    try:
        from . import knowledge
        return knowledge.stats()
    except Exception:
        return {"total": 0, "by_kind": {}, "by_subject": {}}


def _get_appliance_status() -> dict:
    """Appliance monitor state — declared profile (with learned watts) and what
    JARVIS is currently tracking — for the Settings → Appliances panel."""
    try:
        from . import appliance_monitor
        st = appliance_monitor.status()
        return {
            "running": st.get("running", False),
            "profile": st.get("profile", []),
            "tracked_sensors": [
                {"entity": eid, "name": s.get("friendly_name", eid),
                 "appliance": s.get("appliance"), "phase": s.get("phase"),
                 "power_w": round(s.get("power_w", 0) or 0),
                 "discovery": s.get("discovery")}
                for eid, s in (st.get("sensors") or {}).items()
            ],
            "native": [
                {"entity": eid, "name": n.get("device_name", eid),
                 "appliance": n.get("appliance"), "state": n.get("current_state")}
                for eid, n in (st.get("native_appliances") or {}).items()
            ],
            "whole_home": bool(st.get("whole_home_delta")),
        }
    except Exception:
        return {"running": False, "profile": [], "tracked_sensors": [],
                "native": [], "whole_home": False}


def _get_reasoning_stats() -> dict:
    """Learned-reasoning cache + connectivity breaker stats for the panel."""
    out = {
        "learned_patterns": 0, "cloud_calls": 0, "local_decisions": 0,
        "local_rate": 0, "llm_breaker": "closed",
    }
    try:
        from . import reasoning_cache
        out.update(reasoning_cache.stats())
    except Exception:
        pass
    try:
        from . import connectivity
        st = connectivity.status()
        out["llm_breaker"] = st.get("state", "closed") if isinstance(st, dict) else "closed"
    except Exception:
        pass
    return out


def _get_observer_stats() -> dict:
    """Return observer pipeline stats for the tuning dashboard."""
    try:
        from . import observer as obs
        from .database import get_recent_activity
        state = obs._STATE

        # Classifier calls in last hour
        now = time.time()
        calls_last_hour = sum(1 for ts in state.classifier_timestamps if ts > now - 3600) if hasattr(state, 'classifier_timestamps') else 0

        # Activity stats from DB
        recent = get_recent_activity(hours=24, limit=500)
        total_events = len(recent)
        spoken = sum(1 for e in recent if e.get("was_spoken"))
        flagged = sum(1 for e in recent if "flagged" in (e.get("message") or ""))
        dropped = sum(1 for e in recent if "not worth" in (e.get("message") or ""))

        try:
            from . import cognition as _cog
            cog_stats = _cog.stats()
        except Exception:
            cog_stats = {"entities_tracked": 0, "events_seen": 0, "anomalies_escalated": 0}

        try:
            from . import cognition as _cog2
            presence = _cog2.presence_status(state.hass) if getattr(state, "hass", None) else []
        except Exception:
            presence = []

        return {
            "running": state.running,
            "calls_last_hour": calls_last_hour,
            "rate_limit": obs._effective_rate_limit(),
            "events_24h": total_events,
            "flagged_24h": flagged,
            "dropped_24h": dropped,
            "spoken_24h": spoken,
            "cognition_enabled": obs._cognition_enabled(),
            "cognition_threshold": obs._cognition_threshold(),
            "cog_entities": cog_stats.get("entities_tracked", 0),
            "cog_events_seen": cog_stats.get("events_seen", 0),
            "cog_escalated": cog_stats.get("anomalies_escalated", 0),
            "cog_predictable": cog_stats.get("predictable", 0),
            "cog_routines": cog_stats.get("routines", 0),
            "cog_presence": cog_stats.get("presence_routines", 0),
            "presence": presence,
            **_get_reasoning_stats(),
        }
    except Exception:
        return {"running": False, "calls_last_hour": 0, "rate_limit": 30,
                "events_24h": 0, "flagged_24h": 0, "dropped_24h": 0, "spoken_24h": 0,
                "cognition_enabled": True, "cognition_threshold": 0.6,
                "cog_entities": 0, "cog_events_seen": 0, "cog_escalated": 0,
                "cog_predictable": 0, "cog_routines": 0, "cog_presence": 0,
                "presence": [], "learned_patterns": 0, "cloud_calls": 0,
                "local_decisions": 0, "local_rate": 0, "llm_breaker": "closed"}


# ─── Activity log WebSocket command ──────────────────────────────────────────

@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/get_activity_log",
    vol.Optional("hours", default=24): int,
    vol.Optional("limit", default=50): int,
})
@websocket_api.async_response
async def ws_get_activity_log(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return recent activity log entries for the panel."""
    try:
        from .database import get_recent_activity
        entries = await hass.async_add_executor_job(
            lambda: get_recent_activity(hours=msg["hours"], limit=msg["limit"])
        )
        # Format for the panel
        result = []
        for e in entries:
            ts_str = e.get("timestamp", "")
            # Parse "2026-04-23T05:30:00" → "05:30"
            try:
                from datetime import datetime as _dt, timezone as _tz
                from homeassistant.util import dt as dt_util
                # Parse UTC timestamp and convert to local
                dt = _dt.fromisoformat(ts_str).replace(tzinfo=_tz.utc)
                local_dt = dt_util.as_local(dt)
                hhmm = local_dt.strftime("%H:%M")
            except Exception:
                hhmm = ts_str[:5] if len(ts_str) >= 5 else ts_str
            result.append({
                "ts": hhmm,
                "urgency": e.get("urgency", "low"),
                "tag": (e.get("entity_id", "").split(".", 1)[-1][:20] or e.get("source", "")).upper(),
                "msg": e.get("message", ""),
                "source": e.get("source", "observer"),
            })
        connection.send_result(msg["id"], {"entries": result})
    except Exception as exc:
        _LOGGER.warning("ws_get_activity_log failed: %s", exc)
        connection.send_error(msg["id"], "activity_log_failed", str(exc))

# ─── Config update WebSocket command ─────────────────────────────────────────

# Only these keys can be toggled from the panel. Prevents arbitrary writes.
PANEL_WRITABLE_KEYS = {
    "announcements_enabled",
    "sentinel_enabled",
    "observer_enabled",
    "notify_service",
    "disabled_sentinel_rules",   # JSON list of disabled rule IDs
    "satellite_pairings",        # JSON dict: {satellite_entity_id: cast_entity_id}
    "announcement_speakers",     # JSON list of cast entity IDs for announcements
    "floor_plan_rooms",          # JSON: floor plan room positions per floor
    "floor_plan_bg",             # JSON: base64 background images per floor
    "floor_plan_address",        # string: address for OSM map overlay
    # Residence model (the 3D house on the Residence tab)
    "residence_style",           # str: home style template (cape_cod, ranch, …)
    "floor_plan_sqft",           # str/int: estimated square footage
    "home_stories",              # str: number of stories (controls floor tabs)
    "has_basement",              # bool: whether to show the basement floor
    "dormers_front",             # int: front dormer count override
    "dormers_rear",              # int: rear dormer count override
    "garage_bays",               # int: garage bay count
    "chimney_side",              # str: chimney placement (left/right)
    "home_bedrooms",             # int: bedroom count (Residence stats)
    "home_bathrooms",            # int: bathroom count (Residence stats)
    "door_mapping",              # JSON: {model door slot -> entity_id}
    # Outdoor classification (feeds the intrusion false-alarm guards)
    "outdoor_areas",             # JSON list: extra area names treated as outdoor
    "outdoor_entities",          # JSON list: entity globs forced outdoor
    "indoor_entities",           # JSON list: entity globs forced indoor (wins)
    # Web Research + Communication agents (v6.51.0)
    "search_backend",            # str: "duckduckgo" (default) | "searxng"
    "searxng_url",               # str: SearXNG base URL when backend=searxng
    "calendar_tight_gap_min",    # int: back-to-back gap flagged as "tight"
    # Persona (v6.51.0)
    "banter_level",              # int: 0 plain · 1 dry (default) · 2 full MCU wit
    # AI model selection (Settings → AI Models live-fetched dropdowns)
    "llm_provider",
    "model",
    "llm_base_url",
    "classifier_provider",
    "classifier_model",
    "reasoning_provider",
    "reasoning_model",
    "review_provider",
    "review_model",
    "vision_provider",
    "vision_model",
    "camera_reasoning_provider",
    "camera_reasoning_model",
    "classifier_rate_limit",
    "cognition_enabled",
    "cognition_threshold",
    "appliance_profile",            # JSON list of declared appliances (name/type/entity/watts)
    "appliance_announce_unknown",   # bool: announce loads matching no declared appliance
    "camera_auto_analyze",          # bool: auto-inspect doorbell/person camera events
    "camera_auto_analyze_motion",   # bool: also auto-inspect motion events (noisier)
    "package_detection",            # bool: watch porch cameras for packages & mail
    "visitor_learning",             # bool: silent vision learning from person events
    "rich_reasoning",               # bool: cloud-first reasoning for medium+ events
    "llm_base_url",                 # str: OpenAI-compatible endpoint (Ollama GPU server)
    "pattern_min_occurrences",      # int: pattern engine repeat threshold
    "pattern_confidence",           # float: pattern engine confidence threshold
    "light_control_enabled",        # bool: allow toggling lights from the dashboard
    "appliance_power_guessing",     # bool: announce fingerprint/auto-discovered guesses
}

# ── Debug log ring buffer ────────────────────────────────────────────────────
from collections import deque as _deque
from datetime import datetime as _datetime
from pathlib import Path as _Path
import threading as _threading
import queue as _queue
import json as _json_mod

_DEBUG_LOG: _deque = _deque(maxlen=500)
_LOG_FILE = _Path("/config/jarvis/jarvis.log")


def _read_integration_version() -> str:
    """
    Read the integration version from manifest.json — the single source of
    truth. Done once at import (not per-request) so the panel can display the
    actually-running version. This fixes the banner drifting out of sync: the
    version was hardcoded in the panel JS, so a browser-cached panel showed a
    stale number after an addon update. Now the panel fetches this live.
    """
    try:
        mf = _Path(__file__).parent / "manifest.json"
        return _json_mod.loads(mf.read_text()).get("version", "?")
    except Exception:
        return "?"


_INTEGRATION_VERSION = _read_integration_version()

# Persistent-log writes happen on a dedicated daemon thread, never on the
# event loop. jarvis_log() is called synchronously from event-loop callbacks
# (the classifier, observer, etc.) — doing file I/O there blocks the loop, and
# under an announcement/classify storm that stall degrades the ESPHome
# satellite connections (mic ESP_ERR_TIMEOUT → crash-loop). Enqueue instead;
# the writer thread does the blocking open()/write()/rotate() off-loop.
_LOG_QUEUE: "_queue.Queue[dict]" = _queue.Queue(maxsize=2000)
_WRITER_STARTED = False
_WRITER_LOCK = _threading.Lock()


def _log_writer_loop() -> None:
    """Drain the log queue and write to disk. Runs on a daemon thread."""
    while True:
        entry = _LOG_QUEUE.get()
        try:
            if entry is None:
                continue
            _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(_LOG_FILE, "a") as f:
                f.write(f"{entry['date']} {entry['ts']} [{entry['cat']}] {entry['msg']}\n")
            # Rotate if file gets too large (>2MB)
            if _LOG_FILE.stat().st_size > 2_000_000:
                lines = _LOG_FILE.read_text().splitlines()
                _LOG_FILE.write_text("\n".join(lines[-2000:]) + "\n")
        except Exception:
            pass
        finally:
            _LOG_QUEUE.task_done()


def _ensure_writer() -> None:
    """Start the background writer thread once, lazily."""
    global _WRITER_STARTED
    if _WRITER_STARTED:
        return
    with _WRITER_LOCK:
        if _WRITER_STARTED:
            return
        t = _threading.Thread(
            target=_log_writer_loop, name="jarvis-log-writer", daemon=True,
        )
        t.start()
        _WRITER_STARTED = True


def _persist_log_entry(entry: dict) -> None:
    """Queue a log entry for the background writer (never blocks the caller)."""
    _ensure_writer()
    try:
        _LOG_QUEUE.put_nowait(entry)
    except _queue.Full:
        pass  # under extreme load, drop the persisted copy rather than block


def _load_persisted_log() -> None:
    """Load recent entries from persistent log on startup."""
    try:
        if _LOG_FILE.exists():
            import re
            lines = _LOG_FILE.read_text().splitlines()[-200:]
            for line in lines:
                m = re.match(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) \[(\w+)\] (.+)", line)
                if m:
                    _DEBUG_LOG.append({
                        "date": m.group(1),
                        "ts": m.group(2),
                        "cat": m.group(3),
                        "msg": m.group(4),
                    })
    except Exception:
        pass


# Load on import
_load_persisted_log()


def jarvis_log(category: str, message: str) -> None:
    """Add to JARVIS debug log (visible in panel Log tab + persistent file)."""
    now = _datetime.now()
    entry = {
        "date": now.strftime("%Y-%m-%d"),
        "ts": now.strftime("%H:%M:%S"),
        "cat": category,
        "msg": message[:500],
    }
    _DEBUG_LOG.append(entry)
    _persist_log_entry(entry)


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/reload_appliances",
})
@websocket_api.async_response
async def ws_reload_appliances(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Restart the appliance monitor so profile edits take effect immediately
    (no Home Assistant restart needed)."""
    try:
        from . import appliance_monitor
        entry = _get_entry(hass)
        cfg: dict = {}
        if entry:
            cfg = {**dict(entry.data), **dict(entry.options)}
            data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            rc = data.get("runtime_config", {}) if isinstance(data, dict) else {}
            if isinstance(rc, dict):
                cfg.update(rc)
        await appliance_monitor.start(hass, cfg)
        connection.send_result(msg["id"], {
            "ok": True, "appliances": _get_appliance_status(),
        })
    except Exception as exc:
        connection.send_error(msg["id"], "reload_failed", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/set_lockdown",
    vol.Required("on"): bool,
})
@websocket_api.async_response
async def ws_set_lockdown(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Engage or lift the formal lockdown from the panel."""
    try:
        from . import cognitive_core
        ok = await cognitive_core.request_lockdown(
            bool(msg["on"]), reason="requested from panel", hass=hass)
        status = cognitive_core.lockdown_status()
        if not ok:
            _LOGGER.warning("Panel lockdown request returned not-ok (on=%s); status=%s",
                            bool(msg["on"]), status)
        connection.send_result(msg["id"], {"ok": ok, "lockdown": status})
    except Exception as exc:
        _LOGGER.exception("Panel lockdown request failed: %s", exc)
        connection.send_error(msg["id"], "lockdown_failed", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/get_knowledge",
    vol.Optional("subject"): str,
})
@websocket_api.async_response
async def ws_get_knowledge(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the curated facts JARVIS knows, for the Memory panel."""
    try:
        from . import knowledge
        subject = msg.get("subject")
        facts = await hass.async_add_executor_job(lambda: knowledge.all_facts(subject=subject))
        kstats = await hass.async_add_executor_job(knowledge.stats)
        connection.send_result(msg["id"], {"facts": facts, "stats": kstats})
    except Exception as exc:
        _LOGGER.exception("get_knowledge failed: %s", exc)
        connection.send_error(msg["id"], "knowledge_failed", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/add_knowledge",
    vol.Required("key"): str,
    vol.Required("value"): str,
    vol.Optional("subject"): str,
    vol.Optional("kind"): str,
})
@websocket_api.async_response
async def ws_add_knowledge(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Teach JARVIS a fact from the Memory panel."""
    try:
        from . import knowledge
        f = await hass.async_add_executor_job(
            lambda: knowledge.remember(
                msg["key"], msg["value"],
                subject=msg.get("subject", knowledge.DEFAULT_SUBJECT),
                kind=msg.get("kind", "fact"), source="stated"))
        facts = await hass.async_add_executor_job(knowledge.all_facts)
        connection.send_result(msg["id"], {"ok": bool(f), "facts": facts})
    except Exception as exc:
        _LOGGER.exception("add_knowledge failed: %s", exc)
        connection.send_error(msg["id"], "add_failed", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/forget_knowledge",
    vol.Optional("fact_id"): int,
    vol.Optional("subject"): str,
    vol.Optional("key"): str,
})
@websocket_api.async_response
async def ws_forget_knowledge(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Forget a fact (by fact_id, or subject+key) from the Memory panel.

    NOTE: the fact id is carried as ``fact_id``, not ``id`` — ``id`` is reserved
    by the HA WebSocket protocol for the message sequence number (the frontend
    overwrites any ``id`` we send), so using it here silently deleted nothing.
    """
    try:
        from . import knowledge
        fid = msg.get("fact_id")
        removed = await hass.async_add_executor_job(
            lambda: knowledge.forget(fact_id=fid, subject=msg.get("subject"), key=msg.get("key")))
        facts = await hass.async_add_executor_job(knowledge.all_facts)
        connection.send_result(msg["id"], {"removed": removed, "facts": facts})
    except Exception as exc:
        _LOGGER.exception("forget_knowledge failed: %s", exc)
        connection.send_error(msg["id"], "forget_failed", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/root_cause",
    vol.Required("entity_id"): str,
    vol.Optional("event_time"): str,
    vol.Optional("window_secs"): int,
})
@websocket_api.async_response
async def ws_root_cause(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Root cause analysis for an entity's (latest or specified) change —
    the same engine the conversational 'why did …' tool uses, structured for
    the panel."""
    try:
        from . import rca
        result = await hass.async_add_executor_job(
            lambda: rca.analyze(
                msg["entity_id"],
                msg.get("event_time"),
                int(msg.get("window_secs") or rca.DEFAULT_WINDOW_SECS)))
        connection.send_result(msg["id"], result)
    except Exception as exc:
        _LOGGER.exception("root_cause failed: %s", exc)
        connection.send_error(msg["id"], "root_cause_failed", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/update_config",
    vol.Required("key"): str,
    vol.Required("value"): vol.Any(bool, str, int, float, None),
})
@websocket_api.async_response
async def ws_update_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """
    Update a config toggle from the panel.

    Stores in hass.data runtime_config (NOT entry.options) to avoid
    triggering an entry reload which would navigate the browser away
    from the panel. Sentinel and observer check runtime_config first,
    then fall back to entry.options.
    """
    key = msg["key"]
    value = msg["value"]

    if key not in PANEL_WRITABLE_KEYS:
        connection.send_error(
            msg["id"], "invalid_key",
            f"Key '{key}' is not writable from the panel",
        )
        return

    entry = _get_entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "no_entry", "No JARVIS config entry found")
        return

    try:
        # Store in runtime_config — does NOT trigger entry reload
        data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        if data is None:
            connection.send_error(msg["id"], "no_data", "JARVIS runtime data not found")
            return
        rc = data.setdefault("runtime_config", {})
        rc[key] = value
        _LOGGER.info("JARVIS panel: set %s = %s", key, str(value)[:80])

        # Persist via centralized config module (survives restarts)
        try:
            from . import jarvis_config
            await hass.async_add_executor_job(jarvis_config.set, key, value)
        except Exception as exc:
            _LOGGER.debug("Config persist note: %s", exc)

        # If toggling observer, start/stop immediately
        if key == "observer_enabled":
            from . import observer as observer_mod
            if value:
                observer_config = {**dict(entry.data), **dict(entry.options), **rc}
                await observer_mod.start(hass, observer_config)
                data["observer_running"] = True
            else:
                await observer_mod.stop()
                data["observer_running"] = False

        connection.send_result(msg["id"], {"key": key, "value": value})
    except Exception as exc:
        _LOGGER.warning("ws_update_config failed: %s", exc)
        connection.send_error(msg["id"], "update_failed", str(exc))


def _resolve_provider_key(hass: HomeAssistant, entry, provider: str) -> str:
    """Resolve the stored API key for a provider from config."""
    if provider == "gemini":
        return str(_runtime_opt(hass, entry, "gemini_api_key", "") or "")
    # groq/openai/anthropic/custom all use the primary key field
    key = _runtime_opt(hass, entry, "api_key", None)
    if not key:
        key = _runtime_opt(hass, entry, "groq_api_key", "")
    return str(key or "")


async def _fetch_models(hass, provider: str, api_key: str, base_url: str) -> list[str]:
    """
    Query a provider's models endpoint and return a sorted list of model IDs.
    Uses HA's shared aiohttp session (off-loop network I/O). Each provider has
    a different endpoint/auth/response shape; we normalise to a list of strings.
    """
    from homeassistant.helpers import aiohttp_client
    import async_timeout

    session = aiohttp_client.async_get_clientsession(hass)
    provider = (provider or "").lower()
    url = ""
    headers: dict = {}

    if provider == "groq":
        url = "https://api.groq.com/openai/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}
    elif provider == "openai":
        url = "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}
    elif provider == "anthropic":
        url = "https://api.anthropic.com/v1/models"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    elif provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    elif provider in ("ollama", "custom"):
        base = (base_url or "").rstrip("/")
        if not base:
            raise ValueError("base URL required for this provider")
        # Ollama exposes /api/tags; an OpenAI-compatible base exposes /v1/models.
        if base.endswith("/v1"):
            url = f"{base}/models"
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        else:
            url = f"{base}/api/tags"
    else:
        raise ValueError(f"unknown provider: {provider}")

    async with async_timeout.timeout(12):
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"HTTP {resp.status}: {body[:160]}")
            data = await resp.json()

    # Normalise per provider
    models: list[str] = []
    if provider == "gemini":
        for m in data.get("models", []):
            name = m.get("name", "")
            if name.startswith("models/"):
                name = name[len("models/"):]
            # only generative chat models
            methods = m.get("supportedGenerationMethods", [])
            if name and (not methods or "generateContent" in methods):
                models.append(name)
    elif provider in ("ollama", "custom") and url.endswith("/api/tags"):
        for m in data.get("models", []):
            n = m.get("name")
            if n:
                models.append(n)
    else:
        # OpenAI-compatible shape: {"data": [{"id": ...}, ...]}
        for m in data.get("data", []):
            mid = m.get("id")
            if mid:
                models.append(mid)

    return sorted(set(models))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/list_models",
    vol.Required("provider"): str,
    vol.Optional("base_url"): str,
})
@websocket_api.async_response
async def ws_list_models(hass: HomeAssistant, connection, msg) -> None:
    """Return the live model list for a provider (Settings AI-Models dropdowns)."""
    provider = (msg.get("provider") or "").lower()
    entry = _get_entry(hass)
    api_key = _resolve_provider_key(hass, entry, provider)
    base_url = msg.get("base_url") or str(_runtime_opt(hass, entry, "llm_base_url", "") or "")
    try:
        models = await _fetch_models(hass, provider, api_key, base_url)
        connection.send_result(msg["id"], {"provider": provider, "models": models})
    except Exception as exc:
        _LOGGER.info("list_models(%s) failed: %s", provider, exc)
        connection.send_result(
            msg["id"], {"provider": provider, "models": [], "error": str(exc)},
        )

def _get_memory_stats() -> dict:
    """Return memory system stats for the panel."""
    try:
        from .memory import get_memory_stats
        return get_memory_stats()
    except Exception:
        return {"backend": "unavailable", "total_memories": 0}


def _get_runtime_json(hass: HomeAssistant, entry, key: str, default):
    """Read a JSON-encoded value from runtime_config → jarvis_config → entry options."""
    import json as _json
    # 1. In-memory runtime_config (fastest)
    if entry is not None:
        data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        rc = data.get("runtime_config", {}) if isinstance(data, dict) else {}
        raw = rc.get(key)
        if raw is not None:
            if isinstance(raw, (dict, list)):
                return raw
            try:
                return _json.loads(raw)
            except Exception:
                pass

    # 2. Persistent config file (survives restarts)
    try:
        from . import jarvis_config
        val = jarvis_config.get(key)
        if val is not None:
            if isinstance(val, (dict, list)):
                return val
            try:
                return _json.loads(val)
            except Exception:
                return val
    except Exception:
        pass

    # 3. Entry options (bootstrap defaults)
    if entry is not None:
        raw = _entry_opt(entry, key, None)
        if raw is not None:
            if isinstance(raw, (dict, list)):
                return raw
            try:
                return _json.loads(raw)
            except Exception:
                pass

    return default


def _get_runtime_str(hass: HomeAssistant, entry, key: str, default: str) -> str:
    """Read a plain string from runtime_config → jarvis_config → entry options."""
    # 1. In-memory runtime_config
    if entry is not None:
        data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        rc = data.get("runtime_config", {}) if isinstance(data, dict) else {}
        raw = rc.get(key)
        if raw is not None:
            return str(raw)

    # 2. Persistent config file
    try:
        from . import jarvis_config
        val = jarvis_config.get(key)
        if val is not None:
            return str(val)
    except Exception:
        pass

    # 3. Entry options
    if entry is not None:
        raw = _entry_opt(entry, key, None)
        if raw is not None:
            return str(raw)

    return default# ─── Memory search WebSocket command ─────────────────────────────────────────

@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/search_memory",
    vol.Required("query"): str,
    vol.Optional("k", default=5): int,
})
@websocket_api.async_response
async def ws_search_memory(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Search long-term memory for relevant past conversations."""
    try:
        from .memory import search_memory
        results = await hass.async_add_executor_job(
            lambda: search_memory(msg["query"], k=msg["k"])
        )
        connection.send_result(msg["id"], {"results": results})
    except Exception as exc:
        _LOGGER.warning("ws_search_memory failed: %s", exc)
        connection.send_error(msg["id"], "search_failed", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/get_debug_log",
})
@websocket_api.async_response
async def ws_get_debug_log(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return JARVIS internal debug log entries."""
    connection.send_result(msg["id"], {"entries": list(_DEBUG_LOG)})


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/get_cognitive_status",
})
@websocket_api.async_response
async def ws_get_cognitive_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return JARVIS cognitive core status for the dashboard."""
    try:
        from . import cognitive_core
        status = cognitive_core.status()
        connection.send_result(msg["id"], status)
    except Exception as exc:
        connection.send_result(msg["id"], {
            "running": False,
            "error": str(exc),
            "learning": {},
        })


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/suggestion_action",
    vol.Required("suggestion_id"): int,
    vol.Required("action"): vol.In(["approve", "dismiss"]),
})
@websocket_api.async_response
async def ws_suggestion_action(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Approve or dismiss a pattern-engine automation suggestion. Approval now
    installs the automation into HA, not just flags it (v6.52.0)."""
    try:
        from .pattern_analyzer import get_analyzer, install_approved_suggestion
        analyzer = get_analyzer()
        sid = int(msg["suggestion_id"])
        if msg["action"] == "approve":
            res = await install_approved_suggestion(hass, sid)
            if res.get("installed"):
                jarvis_log("LEARN", f"Suggestion #{sid} approved & installed "
                                    f"as '{res.get('alias')}'")
            elif res.get("ok"):
                jarvis_log("LEARN", f"Suggestion #{sid} approved "
                                    f"(advisory — {res.get('reason')})")
            connection.send_result(msg["id"], {
                "ok": bool(res.get("ok")),
                "installed": bool(res.get("installed")),
                "reason": res.get("reason"),
                "alias": res.get("alias"),
            })
            return
        ok = await hass.async_add_executor_job(analyzer.dismiss_suggestion, sid)
        jarvis_log("LEARN", f"Suggestion #{sid} dismissed (ok={ok})")
        connection.send_result(msg["id"], {"ok": bool(ok)})
    except Exception as exc:
        _LOGGER.exception("ws_suggestion_action failed: %s", exc)
        connection.send_error(msg["id"], "suggestion_action_failed", str(exc))


_SNAP_LOG_TS: dict[str, float] = {}


def _snap_log(entity_id: str, msg: str) -> None:
    """CAMERA-log a snapshot failure at most once per 5 min per entity —
    the panel polls this tier every 6s, and a broken camera shouldn't
    flood the log while still leaving a visible trail."""
    now = time.time()
    if now - _SNAP_LOG_TS.get(entity_id, 0) < 300:
        return
    _SNAP_LOG_TS[entity_id] = now
    jarvis_log("CAMERA", f"{entity_id} snapshot: {msg}")


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/camera_snapshot",
    vol.Required("entity_id"): str,
})
@websocket_api.async_response
async def ws_camera_snapshot(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """A frame via JARVIS's camera backend registry (Nest event media,
    Frigate snapshot, stream-wake). The panel's last-resort tile source for
    cameras where /api/camera_proxy* fails — WebRTC-only Nest cams have no
    MJPEG stream and can't produce stills while idle, so both proxy tiers
    404 and the tile went permanently blank (v6.46.0)."""
    import base64
    entity_id = str(msg["entity_id"])
    try:
        if not hass.states.get(entity_id) or not entity_id.startswith("camera."):
            connection.send_error(msg["id"], "unknown_camera", entity_id)
            return
        from . import camera as cam
        img = await cam._get_best_image(hass, entity_id)
        if not img:
            _snap_log(entity_id,
                      "no frame — backend and proxy paths all empty "
                      "(Nest: check integration is loaded and events enabled)")
            connection.send_result(msg["id"], {"image": None})
            return
        img = cam._downscale_jpeg(img, 960)
        connection.send_result(
            msg["id"], {"image": base64.b64encode(img).decode()})
    except Exception as exc:
        _LOGGER.debug("camera_snapshot failed for %s: %s", entity_id, exc)
        _snap_log(entity_id, f"error — {exc}")
        connection.send_error(msg["id"], "snapshot_failed", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/rename_camera",
    vol.Required("entity_id"): str,
    vol.Required("name"): vol.Any(str, None),
})
@websocket_api.async_response
async def ws_rename_camera(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Set a JARVIS-only display name for a camera (v6.48.0) — chips, strip,
    and pickers use it; HA's entity name is untouched. Blank name reverts."""
    entity_id = str(msg["entity_id"])
    new_name = msg.get("name")
    try:
        if not entity_id.startswith("camera.") or not hass.states.get(entity_id):
            connection.send_error(msg["id"], "unknown_camera", entity_id)
            return
        from . import jarvis_config
        from .camera import merge_camera_name
        names = merge_camera_name(_get_camera_names(), entity_id, new_name)
        await hass.async_add_executor_job(jarvis_config.set, "camera_names", names)
        shown = names.get(entity_id)
        jarvis_log("CONFIG", f"camera {entity_id} "
                             + (f"renamed to '{shown}'" if shown else "name reverted")
                             + " (JARVIS only)")
        connection.send_result(msg["id"], {
            "ok": True, "camera_names": names, "cameras": _get_cameras(hass),
        })
    except Exception as exc:
        _LOGGER.exception("rename_camera failed: %s", exc)
        connection.send_error(msg["id"], "rename_failed", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/vector_backend",
    vol.Required("action"): vol.In(["status", "install"]),
})
@websocket_api.async_response
async def ws_vector_backend(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Optional ChromaDB vector backend control (v6.56.0): report status, or
    install it at runtime to upgrade memory + document retrieval from keyword
    (FTS5) to true semantic vector search — no HA restart needed."""
    try:
        from . import vector_backend
        if msg["action"] == "install":
            res = await vector_backend.install(hass)
            if res.get("ok") and res.get("installed"):
                jarvis_log("AGENT", "vector backend (ChromaDB) "
                                    + ("already active" if res.get("already")
                                       else "installed and activated"))
            elif res.get("error"):
                jarvis_log("AGENT", f"vector backend install failed: {res['error']}")
            connection.send_result(msg["id"], res)
        else:
            res = await hass.async_add_executor_job(vector_backend.status)
            connection.send_result(msg["id"], res)
    except Exception as exc:
        _LOGGER.exception("ws_vector_backend failed: %s", exc)
        connection.send_error(msg["id"], "vector_backend_failed", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/documents",
    vol.Required("action"): vol.In(["status", "ingest", "search"]),
    vol.Optional("query"): str,
})
@websocket_api.async_response
async def ws_documents(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Document library control for the panel (v6.55.0): status (what's
    ingested), ingest (re-scan /config/jarvis/documents), search (test a
    query). The retrieval JARVIS actually uses in conversation is the
    search_documents agent tool; this exposes the same store to the UI."""
    action = msg["action"]
    try:
        from . import documents
        if action == "status":
            res = await hass.async_add_executor_job(documents.library_status)
        elif action == "ingest":
            res = await hass.async_add_executor_job(documents.ingest_directory)
            jarvis_log("AGENT", f"documents ingested via panel: "
                                f"{res.get('files_ingested',0)} files, "
                                f"{res.get('total_chunks',0)} chunks")
        else:  # search
            hits = await hass.async_add_executor_job(
                documents.search_documents, msg.get("query", ""), 5)
            res = {"results": hits}
        connection.send_result(msg["id"], res)
    except Exception as exc:
        _LOGGER.exception("ws_documents failed: %s", exc)
        connection.send_error(msg["id"], "documents_failed", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/mmwave_overview",
})
@websocket_api.async_response
async def ws_mmwave_overview(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Per-area mmWave presence overview for the residence tab (v6.53.0).

    Distinct from the generic area grid: this reports *only* rooms with
    presence/occupancy/motion sensors, and for each the live sensor breakdown —
    how many sensors, how many currently detecting, the freshest detection age —
    so the panel can show genuine mmWave coverage and live state rather than a
    binary 'occupied' flag that could come from a door contact."""
    import time as _t
    try:
        from . import audio_routing
        rooms = []
        total_sensors = 0
        rooms_detecting = 0
        for aid in _all_areas_with_anything(hass):
            sensors = audio_routing.presence_entities_in_area(hass, aid)
            if not sensors:
                continue
            detecting = 0
            freshest = None            # seconds since most-recent change
            sensor_rows = []
            for eid in sensors:
                st = hass.states.get(eid)
                if st is None:
                    continue
                on = st.state == "on"
                if on:
                    detecting += 1
                age = None
                try:
                    age = _t.time() - st.last_changed.timestamp()
                    if freshest is None or age < freshest:
                        freshest = age
                except Exception:
                    pass
                sensor_rows.append({
                    "entity_id": eid,
                    "name": (st.attributes.get("friendly_name") or eid),
                    "detecting": on,
                    "age": _format_duration(age),
                })
            total_sensors += len(sensor_rows)
            if detecting:
                rooms_detecting += 1
            rooms.append({
                "area_id": aid,
                "name": _area_name(hass, aid),
                "outdoor": _is_outdoor_area(hass, aid),
                "sensor_count": len(sensor_rows),
                "detecting_count": detecting,
                "state": ("detecting" if detecting else "clear"),
                "freshest": _format_duration(freshest),
                "sensors": sensor_rows,
            })
        # Detecting rooms first, then most-recently-active, then name
        rooms.sort(key=lambda r: (r["detecting_count"] == 0, r["name"].lower()))
        connection.send_result(msg["id"], {
            "rooms": rooms,
            "summary": {
                "rooms_with_mmwave": len(rooms),
                "rooms_detecting": rooms_detecting,
                "total_sensors": total_sensors,
            },
        })
    except Exception as exc:
        _LOGGER.exception("mmwave_overview failed: %s", exc)
        connection.send_error(msg["id"], "mmwave_overview_failed", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/camera_location",
    vol.Required("entity_id"): str,
    vol.Required("mode"): vol.In(["auto", "indoor", "outdoor"]),
})
@websocket_api.async_response
async def ws_camera_location(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Designate a camera indoor/outdoor (or auto = heuristics), v6.49.0.
    Pins the exact entity id into the existing indoor_entities /
    outdoor_entities lists — outdoor.py's most-authoritative layer — so the
    designation immediately governs the intrusion investigator, the
    notable-outdoor-event filter, and the motion scan alike."""
    entity_id = str(msg["entity_id"])
    mode = str(msg["mode"])
    try:
        if not entity_id.startswith("camera.") or not hass.states.get(entity_id):
            connection.send_error(msg["id"], "unknown_camera", entity_id)
            return
        from . import jarvis_config, outdoor
        new_in, new_out = outdoor.set_entity_location(
            outdoor._cfg_list("indoor_entities"),
            outdoor._cfg_list("outdoor_entities"),
            entity_id, mode,
        )
        await hass.async_add_executor_job(
            jarvis_config.set_many,
            {"indoor_entities": new_in, "outdoor_entities": new_out},
        )
        jarvis_log("CONFIG", f"camera {entity_id} location → {mode.upper()}"
                             + ("" if mode != "auto" else " (heuristics)"))
        connection.send_result(msg["id"], {
            "ok": True, "cameras": _get_cameras(hass),
        })
    except Exception as exc:
        _LOGGER.exception("camera_location failed: %s", exc)
        connection.send_error(msg["id"], "camera_location_failed", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/camera_diagnostics",
    vol.Optional("entity_id"): str,
})
@websocket_api.async_response
async def ws_camera_diagnostics(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """End-to-end probe of one camera's frame sources, plus a platform
    summary of every camera entity HA has — answers both "why is this tile
    blank" and "do my Nest entities even exist" in one call (v6.46.2)."""
    import asyncio as _aio
    try:
        summary = []
        platforms: dict[str, int] = {}
        try:
            from homeassistant.helpers import entity_registry as er
            reg = er.async_get(hass)
        except Exception:
            reg = None
        for st in hass.states.async_all("camera"):
            plat = None
            if reg:
                try:
                    e = reg.async_get(st.entity_id)
                    plat = e.platform if e else None
                except Exception:
                    plat = None
            platforms[plat or "?"] = platforms.get(plat or "?", 0) + 1
            summary.append({"entity_id": st.entity_id,
                            "state": st.state, "platform": plat})

        probe = None
        entity_id = msg.get("entity_id")
        if entity_id:
            from . import camera as cam
            try:
                probe = await _aio.wait_for(
                    cam.probe_camera(hass, str(entity_id)), timeout=30)
            except _aio.TimeoutError:
                probe = {"entity_id": entity_id, "tiers": [],
                         "verdict": "probe timed out after 30s "
                                    "(stream wake hanging?)"}
            jarvis_log("CAMERA", f"diag {entity_id}: {probe.get('verdict', '?')}")

        connection.send_result(msg["id"], {
            "summary": summary, "platforms": platforms, "probe": probe,
        })
    except Exception as exc:
        _LOGGER.exception("camera_diagnostics failed: %s", exc)
        connection.send_error(msg["id"], "camera_diag_failed", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/get_area_sparklines",
})
@websocket_api.async_response
async def ws_get_area_sparklines(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Recent temp/humidity history per area, for dashboard sparklines.
    Deliberately a separate, slow-polled command — recorder history queries
    are heavier than the rest of the panel payload and shouldn't ride along
    on the fast real-time-triggered refresh."""
    try:
        entity_map: dict[str, dict[str, Optional[str]]] = {}
        for aid in _all_areas_with_anything(hass):
            t_eid, h_eid = _area_temp_humidity_entities(hass, aid)
            if t_eid or h_eid:
                entity_map[aid] = {"temp": t_eid, "humidity": h_eid}
        sparklines = await _get_area_sparklines(hass, entity_map)
        connection.send_result(msg["id"], {"sparklines": sparklines})
    except Exception as exc:
        _LOGGER.exception("get_area_sparklines failed: %s", exc)
        connection.send_error(msg["id"], "sparklines_failed", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/goal_action",
    vol.Required("goal_id"): int,
    vol.Required("action"): vol.In(["cancel"]),
})
@websocket_api.async_response
async def ws_goal_action(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Cancel an active goal from the panel. (Only 'cancel' today — goals
    otherwise close themselves via the headless runner.)"""
    try:
        from . import goals
        gid = int(msg["goal_id"])
        ok = await hass.async_add_executor_job(goals.cancel, gid)
        jarvis_log("LEARN", f"Goal #{gid} cancelled from panel (ok={ok})")
        connection.send_result(msg["id"], {"ok": bool(ok), "goals": _get_goals()})
    except Exception as exc:
        _LOGGER.exception("ws_goal_action failed: %s", exc)
        connection.send_error(msg["id"], "goal_action_failed", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "jarvis/get_person_routines",
})
@websocket_api.async_response
async def ws_get_person_routines(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Per-person learned routines, grouped by person, for the Memory panel."""
    try:
        routines = await hass.async_add_executor_job(_get_person_routines)
        connection.send_result(msg["id"], {"routines": routines})
    except Exception as exc:
        _LOGGER.exception("get_person_routines failed: %s", exc)
        connection.send_error(msg["id"], "person_routines_failed", str(exc))
