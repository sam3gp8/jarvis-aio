"""
JARVIS whole-house energy management (v6.62.0).

The appliance monitor already SENSES power — it finds the whole-home meter,
fingerprints appliances by wattage, and tracks what's running. This module is
the MANAGEMENT layer on top: it reads current draw, understands what's drawing,
and helps run the house efficiently — surfacing high-draw situations, warning
before a configured peak, and (at higher agency levels) deferring or staggering
high-draw loads.

Configurable agency ladder (energy_agency config key):
  - "advisory"  (default) — only surfaces insights; never touches a load.
  - "opt_in"              — may propose deferring/staggering a load; acts only
                            after approval (routed through the normal suggestion
                            path — no new approval UI).
  - "autonomous"          — may auto-defer high-draw loads when over the peak
                            threshold, within user-set limits.

Mode-aware: the active operational mode can raise the effective agency — e.g.
"away" enables more aggressive saving than the household would want while home.
A mode never RAISES agency past what the user configured as their ceiling; it
can only be as aggressive as `energy_agency` permits. Safety is never touched:
this module never sheds a load tagged critical (medical, security, network) and
never acts on anything not explicitly an appliance/high-draw device.

Nothing here raises to the caller; every entry point returns a status dict.
"""
from __future__ import annotations

import logging
from typing import Optional

_LOGGER = logging.getLogger(__name__)

AGENCY_ADVISORY = "advisory"
AGENCY_OPT_IN = "opt_in"
AGENCY_AUTONOMOUS = "autonomous"
_AGENCY_ORDER = {AGENCY_ADVISORY: 0, AGENCY_OPT_IN: 1, AGENCY_AUTONOMOUS: 2}

# Default peak threshold (watts) above which the home is "drawing heavily".
_DEFAULT_PEAK_W = 8000.0

# Loads we never shed regardless of agency — matched against entity_id/name.
_NEVER_SHED = ("medical", "cpap", "oxygen", "fridge", "freezer", "refrigerator",
               "security", "alarm", "network", "router", "modem", "server",
               "sump", "well_pump", "furnace", "boiler", "heat")


def _cfg(key: str, default=None):
    try:
        from . import jarvis_config
        v = jarvis_config.get(key, default)
        return v if v is not None else default
    except Exception:
        return default


def _configured_agency() -> str:
    a = str(_cfg("energy_agency", AGENCY_ADVISORY) or AGENCY_ADVISORY).lower()
    return a if a in _AGENCY_ORDER else AGENCY_ADVISORY


def _mode_agency_bonus() -> int:
    """A mode may nudge agency up by one step (e.g. 'away'). Returns 0 or 1.
    Never lets the effective level exceed the user's configured ceiling."""
    try:
        from . import modes
        # Which modes are allowed to bump energy agency — configurable, with a
        # sensible default (away/focus lean toward saving).
        bump_modes = _cfg("energy_mode_bump", [])  # opt-in: empty = mode never changes agency
        if isinstance(bump_modes, list) and modes.active_mode() in bump_modes:
            return 1
    except Exception:
        pass
    return 0


def effective_agency() -> str:
    """The agency level in force right now.

    Baseline is exactly what the user configured in `energy_agency` — no
    surprises: choose 'autonomous' and you get autonomy, choose 'advisory' and
    JARVIS only ever advises. Separately, a mode listed in `energy_mode_bump`
    (e.g. 'away') can raise the level ONE step for the duration of that mode —
    letting the house save more aggressively while you're out. The bump is
    additive and purely opt-in (empty bump list → mode never changes agency),
    and it's capped at 'autonomous'. A user who wants zero mode influence simply
    leaves `energy_mode_bump` empty.
    """
    base_rank = _AGENCY_ORDER[_configured_agency()]
    effective_rank = min(base_rank + _mode_agency_bonus(), _AGENCY_ORDER[AGENCY_AUTONOMOUS])
    for name, rank in _AGENCY_ORDER.items():
        if rank == effective_rank:
            return name
    return _configured_agency()


def _peak_threshold() -> float:
    try:
        return float(_cfg("energy_peak_watts", _DEFAULT_PEAK_W) or _DEFAULT_PEAK_W)
    except (ValueError, TypeError):
        return _DEFAULT_PEAK_W


def _never_shed(entity_id: str, name: str) -> bool:
    hay = f"{entity_id} {name}".lower()
    return any(tok in hay for tok in _NEVER_SHED)


# ── current power picture (reuses appliance_monitor's meter discovery) ───────

def _read_whole_home_watts(hass) -> tuple[Optional[float], str]:
    """Current whole-home draw in watts, plus the source entity. Reuses the
    appliance monitor's meter-discovery heuristics. (None, '') if not found."""
    try:
        from . import appliance_monitor
        tracker = appliance_monitor._discover_whole_home_meter(hass)
        # tracker exposes the entity; re-read its live value for freshness
        eid = getattr(tracker, "entity_id", None) if tracker else None
    except Exception:
        eid = None

    if not eid:
        # fallback: scan for a plausible whole-home meter directly
        eid = _fallback_meter(hass)
    if not eid:
        return None, ""

    st = hass.states.get(eid)
    if st is None:
        return None, eid
    try:
        val = float(st.state)
        unit = (st.attributes.get("unit_of_measurement") or "").lower()
        if unit == "kw":
            val *= 1000.0
        return val, eid
    except (ValueError, TypeError):
        return None, eid


def _fallback_meter(hass) -> Optional[str]:
    for state in hass.states.async_all("sensor"):
        dc = state.attributes.get("device_class", "")
        unit = (state.attributes.get("unit_of_measurement") or "").lower()
        fname = (state.attributes.get("friendly_name") or "").lower()
        if dc != "power" and unit not in ("w", "kw"):
            continue
        for kw in ("electric consumption", "home energy", "total consumption",
                   "main power", "whole house", "grid consumption", "mains power"):
            if kw in fname:
                return state.entity_id
    return None


def _running_appliances(hass) -> list[dict]:
    """Declared appliances currently drawing power, from config + live sensors.
    Each: {name, entity, watts, shed_ok}."""
    out = []
    profile = _cfg("appliances", [])
    if not isinstance(profile, list):
        return out
    for ap in profile:
        if not isinstance(ap, dict):
            continue
        name = str(ap.get("name", "") or "")
        eid = str(ap.get("entity", "") or "")
        watts = 0.0
        running = False
        if eid:
            st = hass.states.get(eid)
            if st is not None:
                try:
                    watts = float(st.state)
                    unit = (st.attributes.get("unit_of_measurement") or "").lower()
                    if unit == "kw":
                        watts *= 1000.0
                    running = watts > 50.0     # drawing meaningfully
                except (ValueError, TypeError):
                    pass
        if running:
            out.append({
                "name": name, "entity": eid, "watts": round(watts),
                "shed_ok": not _never_shed(eid, name),
            })
    out.sort(key=lambda a: -a["watts"])
    return out


# ── public: status + advice ──────────────────────────────────────────────────

def power_status(hass) -> dict:
    """The current energy picture for the panel/agent. Never raises."""
    watts, meter = _read_whole_home_watts(hass)
    peak = _peak_threshold()
    running = _running_appliances(hass)
    over = watts is not None and watts >= peak

    advice = []
    if watts is None:
        advice.append("No whole-home power meter found — set one up (an Electric "
                      "Consumption [W] sensor) to enable energy management.")
    elif over:
        big = [a for a in running if a["shed_ok"]]
        if len(big) >= 2:
            names = " + ".join(a["name"] for a in big[:3] if a["name"])
            advice.append(f"Drawing {watts/1000:.1f} kW (over the "
                          f"{peak/1000:.1f} kW peak) — {names or 'multiple loads'} "
                          f"running at once. Staggering them would cut the peak.")
        else:
            advice.append(f"Drawing {watts/1000:.1f} kW, above the "
                          f"{peak/1000:.1f} kW peak.")
    else:
        if watts is not None:
            advice.append(f"Drawing {watts/1000:.1f} kW — within the "
                          f"{peak/1000:.1f} kW comfort range.")

    return {
        "watts": round(watts) if watts is not None else None,
        "kw": round(watts / 1000, 2) if watts is not None else None,
        "meter": meter,
        "peak_watts": round(peak),
        "over_peak": over,
        "agency": effective_agency(),
        "configured_agency": _configured_agency(),
        "running": running,
        "advice": advice,
    }


def evaluate_for_proactive(hass) -> Optional[dict]:
    """Called by the cognitive loop: returns a proactive offer dict when the
    home is over peak with sheddable loads, shaped by the effective agency —
    advisory just informs; opt_in proposes an action; autonomous flags auto.
    Returns None when nothing warrants surfacing. Never raises."""
    try:
        st = power_status(hass)
    except Exception:
        return None
    if not st.get("over_peak"):
        return None
    sheddable = [a for a in st["running"] if a["shed_ok"]]
    if len(sheddable) < 2:
        return None      # a single big load isn't a staggering opportunity

    agency = st["agency"]
    kw = st["kw"]
    names = " and ".join(a["name"] for a in sheddable[:2] if a["name"]) or "two high-draw loads"
    defer = sheddable[0]  # highest-draw sheddable load

    if agency == AGENCY_AUTONOMOUS:
        return {
            "type": "energy_shed", "urgency": "low", "auto_act": True,
            "entity_id": defer["entity"],
            "message": f"{names} are both running at {kw:.1f} kW. I'll ease the "
                       f"peak by holding {defer['name']} for now.",
            "energy": True,
        }
    if agency == AGENCY_OPT_IN:
        return {
            "type": "energy_shed_offer", "urgency": "low", "auto_act": False,
            "entity_id": defer["entity"],
            "message": f"{names} are drawing {kw:.1f} kW together. Want me to "
                       f"hold {defer['name']} until the load drops?",
            "energy": True,
        }
    # advisory
    return {
        "type": "energy_advice", "urgency": "low", "auto_act": False,
        "message": f"Heads up — {names} are running at {kw:.1f} kW, over your "
                   f"peak. Staggering them would help.",
        "energy": True,
    }
