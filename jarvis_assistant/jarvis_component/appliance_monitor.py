"""
JARVIS — Appliance Cycle Monitor (v5.7.05).

Watches power consumption sensors to infer when household tasks finish.
Announces completion through the JARVIS audio pipeline.

Discovery methods (in order):
  1. Keyword match: entity_id or friendly_name contains appliance keywords
  2. Device siblings: power sensor on the same device as a switch/plug named
     after an appliance (e.g. smart plug "Laundry Plug" → power sensor)
  3. Area inference: power sensor in an area named "laundry", "utility", etc.
  4. Power fingerprinting: unknown sensors are tracked and classified by
     their wattage profile once a cycle is observed
  5. Explicit config: user maps sensor → appliance type in addon config

State machine per tracked sensor:
  IDLE ──(power > run_threshold for sustained_seconds)──→ RUNNING
  RUNNING ──(power < idle_threshold for settle_seconds)──→ DONE → announce → IDLE

Power fingerprinting thresholds (for auto-identification):
  Washer:     300-600W peak, cycling/pulsing pattern
  Dryer:      1800-5500W sustained
  Dishwasher: 1200-1800W with cycles, 50-100W wash phases
  Oven:       2000-5000W sustained with thermostat cycling
  Microwave:  600-1500W very steady, short duration (<15min)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from homeassistant.core import HomeAssistant, Event, callback

_LOGGER = logging.getLogger(__name__)


# ── Appliance profiles ──────────────────────────────────────────────────────

class ApplianceType(Enum):
    WASHER      = ("washer",      50,  10, 120, 60)
    DRYER       = ("dryer",       100, 15, 120, 90)
    DISHWASHER  = ("dishwasher",  30,  5,  120, 60)
    OVEN        = ("oven",        500, 20, 180, 120)
    MICROWAVE   = ("microwave",   200, 10, 30,  15)
    GENERIC     = ("appliance",   50,  10, 120, 60)

    def __init__(self, label, run_w, idle_w, sustain_s, settle_s):
        self.label = label
        self.run_threshold = run_w
        self.idle_threshold = idle_w
        self.sustained_seconds = sustain_s
        self.settle_seconds = settle_s


# Keywords → appliance type mapping
_KEYWORDS = {
    "washer": ApplianceType.WASHER,
    "washing_machine": ApplianceType.WASHER,
    "wash_machine": ApplianceType.WASHER,
    "laundry": ApplianceType.WASHER,
    "dryer": ApplianceType.DRYER,
    "clothes_dryer": ApplianceType.DRYER,
    "tumble_dryer": ApplianceType.DRYER,
    "dishwasher": ApplianceType.DISHWASHER,
    "dish_washer": ApplianceType.DISHWASHER,
    "oven": ApplianceType.OVEN,
    "stove": ApplianceType.OVEN,
    "range": ApplianceType.OVEN,
    "microwave": ApplianceType.MICROWAVE,
}

# Area keywords that hint at appliance zones
_AREA_HINTS = {
    "laundry": ApplianceType.WASHER,
    "utility": ApplianceType.WASHER,
    "mud_room": ApplianceType.WASHER,
    "mudroom": ApplianceType.WASHER,
}

# Power fingerprint ranges for auto-identification (peak_w_min, peak_w_max)
_POWER_FINGERPRINTS = [
    (1800, 6000,  ApplianceType.DRYER,      "sustained high draw → likely dryer"),
    (1200, 2000,  ApplianceType.DISHWASHER,  "heating-range draw → likely dishwasher"),
    (600,  1500,  ApplianceType.MICROWAVE,   "medium steady draw → likely microwave"),
    (200,  800,   ApplianceType.WASHER,      "cycling mid-range → likely washer"),
    (2000, 5500,  ApplianceType.OVEN,        "high sustained → likely oven"),
]


def _classify_appliance(entity_id: str, friendly_name: str) -> Optional[ApplianceType]:
    """Identify appliance type from entity_id or friendly_name."""
    search = (entity_id + " " + friendly_name).lower()
    for keyword, atype in _KEYWORDS.items():
        if keyword in search:
            return atype
    return None


def _fingerprint_from_power(peak_watts: float) -> Optional[tuple[ApplianceType, str]]:
    """Guess appliance type from observed peak power draw."""
    for low, high, atype, reason in _POWER_FINGERPRINTS:
        if low <= peak_watts <= high:
            return atype, reason
    if peak_watts > 6000:
        return ApplianceType.OVEN, f"very high draw ({peak_watts:.0f}W) → likely oven/range"
    return None


# ── User-declared appliance profile ─────────────────────────────────────────
# The user confirms which appliances exist (Settings → Appliances). Each entry:
#   {"name": "Washer", "type": "washer", "entity": "sensor.washer_power"|"",
#    "watts": 500}
# A dedicated entity (when set) is authoritative and excluded from whole-home
# disaggregation. Appliances WITHOUT an entity become disaggregation candidates,
# and the whole-home meter is matched ONLY against their declared wattage — so
# JARVIS names an appliance only when the load actually matches a confirmed one,
# instead of guessing every mid-range draw is "the washer".

def _type_to_appliance(type_str: str) -> ApplianceType:
    t = (type_str or "").strip().lower()
    for kw, atype in _KEYWORDS.items():
        if kw == t or kw in t:
            return atype
    for atype in ApplianceType:
        if atype.label == t:
            return atype
    return ApplianceType.GENERIC


def _normalize_profile(config: dict) -> list:
    raw = config.get("appliance_profile", [])
    if isinstance(raw, str):
        try:
            import json as _json
            raw = _json.loads(raw) if raw.strip() else []
        except Exception:
            raw = []
    out = []
    if isinstance(raw, list):
        for it in raw:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name", "")).strip()
            if not name:
                continue
            try:
                watts = float(it.get("watts", 0) or 0)
            except (TypeError, ValueError):
                watts = 0.0
            out.append({
                "name": name,
                "type": str(it.get("type", "appliance")).strip().lower() or "appliance",
                "entity": str(it.get("entity", "")).strip(),
                "watts": watts,
                "learned_w": watts,     # refined from confident observations
                "observed_n": 0,
            })
    return out


def _claimed_running_near(delta_w: float) -> bool:
    """True if a declared appliance with a dedicated sensor is currently running
    at a level near this delta — i.e. the whole-home rise is already explained by
    a known appliance, so we must not also attribute it to a disagg candidate."""
    for s in _MON.sensors.values():
        if s.entity_id in _MON.claimed and s.phase == "running":
            ref = max(s.peak_power, s.last_power)
            if ref > 0 and abs(delta_w - ref) / max(ref, 1.0) <= 0.5:
                return True
    return False


def _match_declared(delta_w: float):
    """
    Match a whole-home power delta to a user-declared appliance that has no
    dedicated entity. Returns (appliance_dict, rel_error, ambiguous) or
    (None, None, False). Tolerance scales with wattage (±35% or ±250W).
    """
    if _claimed_running_near(delta_w):
        return None, None, False
    within = []
    for ap in _MON.disagg:
        w = ap.get("learned_w") or ap.get("watts") or 0
        if w <= 0:
            continue
        err = abs(delta_w - w) / w
        tol = max(0.35, 250.0 / w)
        if err <= tol:
            within.append((err, ap))
    if not within:
        return None, None, False
    within.sort(key=lambda x: x[0])
    best_err, best = within[0]
    ambiguous = len(within) > 1 and (within[1][0] - best_err) < 0.10
    return best, best_err, ambiguous


def _learn_watts(ap: dict, observed_w: float, ambiguous: bool) -> None:
    """Refine an appliance's learned wattage from a confident, unambiguous match
    (EMA). Guarded so a near-miss or ambiguous match can't drift the profile."""
    if ambiguous or observed_w <= 0:
        return
    base = ap.get("learned_w") or ap.get("watts") or observed_w
    if base > 0 and abs(observed_w - base) / base > 0.25:
        return                       # too far off to be the same load — don't learn
    ap["learned_w"] = round(0.8 * base + 0.2 * observed_w, 1)
    ap["observed_n"] = ap.get("observed_n", 0) + 1


# ── Per-sensor state machine ────────────────────────────────────────────────

@dataclass
class _SensorState:
    entity_id: str
    friendly_name: str
    appliance: ApplianceType
    phase: str = "idle"           # idle | running | settling
    run_start: float = 0.0       # when power first exceeded threshold
    settle_start: float = 0.0    # when power first dropped below threshold
    last_power: float = 0.0
    peak_power: float = 0.0      # highest reading in current cycle
    announced: bool = False       # prevent double-announce per cycle
    discovery_method: str = ""    # how this sensor was discovered
    identified: bool = True       # False = GENERIC, needs fingerprinting
    sibling_entities: list = field(default_factory=list)


# ── Native appliance tracking ───────────────────────────────────────────────
# Smart appliances (Samsung, LG, etc.) expose status entities directly.
# Much more reliable than power inference.

@dataclass
class _NativeAppliance:
    """Tracks a smart appliance via its native status entity."""
    entity_id: str             # The status entity (e.g. binary_sensor.washer_run_completed)
    device_name: str           # Human-friendly device name
    appliance: ApplianceType
    trigger_state: str         # State value that means "done" (e.g. "on", "completed")
    last_state: str = ""
    announced: bool = False


# Native status patterns: (keyword_in_entity, trigger_state, appliance_type)
_NATIVE_PATTERNS = [
    ("run_completed",    "on",        None),   # Samsung washer/dryer
    ("run_complete",     "on",        None),
    ("cycle_complete",   "on",        None),
    ("job_state",        "finished",  None),   # LG ThinQ
    ("machine_state",    "idle",      None),   # After running → idle
    ("washer_job_state", "finished",  ApplianceType.WASHER),
    ("dryer_job_state",  "finished",  ApplianceType.DRYER),
    ("dishwasher_job",   "finished",  ApplianceType.DISHWASHER),
]


# ── Whole-home energy delta tracking ────────────────────────────────────────
# Watches total consumption for sudden jumps/drops that match appliance
# power signatures. Used when no per-appliance sensor exists.

@dataclass
class _DeltaTracker:
    """Tracks whole-home power for appliance start/stop detection."""
    entity_id: str
    baseline_w: float = 0.0        # rolling average when "idle"
    samples: list = field(default_factory=list)  # recent readings for averaging
    active_deltas: dict = field(default_factory=dict)
    # active_deltas: {delta_id: {start_time, delta_w, peak_w, appliance_guess}}
    last_reading: float = 0.0
    _next_delta_id: int = 0

    def next_id(self) -> str:
        self._next_delta_id += 1
        return f"delta_{self._next_delta_id}"


# ── Module state ────────────────────────────────────────────────────────────

class _MonitorState:
    def __init__(self):
        self.hass: Optional[HomeAssistant] = None
        self.config: dict = {}
        self.sensors: dict[str, _SensorState] = {}      # power-based tracking
        self.natives: dict[str, _NativeAppliance] = {}   # native status tracking
        self.delta: Optional[_DeltaTracker] = None       # whole-home delta
        self.profile: list = []          # user-declared appliances (name/type/entity/watts)
        self.disagg: list = []           # declared appliances WITHOUT a dedicated entity
        self.claimed: set = set()        # entities owned by a declared appliance
        self.announce_unknown: bool = False  # announce loads that match no declared appliance
        self.unsub = None
        self.running = False

_MON = _MonitorState()


# ── Discovery ───────────────────────────────────────────────────────────────

def _get_device_siblings(hass: HomeAssistant, entity_id: str) -> tuple[
    Optional[str], list[str]
]:
    """
    Find all sibling entities on the same device.
    Returns (device_name, [sibling_entity_ids]).
    """
    try:
        from homeassistant.helpers import (
            entity_registry as er, device_registry as dr,
        )
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)

        entry = ent_reg.async_get(entity_id)
        if not entry or not entry.device_id:
            return None, []

        device = dev_reg.async_get(entry.device_id)
        dev_name = device.name_by_user or device.name or "" if device else ""

        siblings = []
        for ent in ent_reg.entities.values():
            if ent.device_id == entry.device_id and ent.entity_id != entity_id:
                siblings.append(ent.entity_id)

        return dev_name, siblings
    except Exception:
        return None, []


def _get_entity_area(hass: HomeAssistant, entity_id: str) -> Optional[str]:
    """Get the area name for an entity (direct or via device)."""
    try:
        from homeassistant.helpers import (
            area_registry as areg, entity_registry as er, device_registry as dr,
        )
        area_reg = areg.async_get(hass)
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)

        entry = ent_reg.async_get(entity_id)
        if not entry:
            return None

        area_id = entry.area_id
        if not area_id and entry.device_id:
            device = dev_reg.async_get(entry.device_id)
            if device:
                area_id = device.area_id

        if area_id:
            area = area_reg.async_get_area(area_id)
            return area.name.lower() if area else None
    except Exception:
        pass
    return None


def _discover_sensors(hass: HomeAssistant) -> dict[str, _SensorState]:
    """
    Find power sensors that might be appliances using multiple methods.

    Discovery priority:
      1. Direct keyword match on entity_id / friendly_name
      2. Device sibling keyword match (smart plug with appliance name)
      3. Area-based inference (sensor in "laundry" area)
      4. Generic tracking (unidentified, will fingerprint on first cycle)
    """
    found = {}
    unidentified_power = []  # power sensors we couldn't classify

    for state in hass.states.async_all("sensor"):
        dc = state.attributes.get("device_class", "")
        unit = (state.attributes.get("unit_of_measurement") or "").lower()

        if dc != "power" and unit not in ("w", "kw", "watt", "watts"):
            continue

        fname = state.attributes.get("friendly_name", "")
        eid = state.entity_id

        # Method 1: Direct keyword match
        atype = _classify_appliance(eid, fname)
        if atype:
            dev_name, siblings = _get_device_siblings(hass, eid)
            found[eid] = _SensorState(
                entity_id=eid,
                friendly_name=fname or eid,
                appliance=atype,
                discovery_method="keyword",
                sibling_entities=siblings,
            )
            _LOGGER.info(
                "Appliance [keyword]: %s → %s (%s)",
                eid, atype.label, fname,
            )
            continue

        # Method 2: Device sibling keyword match
        dev_name, siblings = _get_device_siblings(hass, eid)
        if dev_name:
            atype = _classify_appliance("", dev_name)
            if atype:
                found[eid] = _SensorState(
                    entity_id=eid,
                    friendly_name=dev_name or fname or eid,
                    appliance=atype,
                    discovery_method="device_sibling",
                    sibling_entities=siblings,
                )
                _LOGGER.info(
                    "Appliance [device]: %s → %s (device='%s', siblings=%s)",
                    eid, atype.label, dev_name, siblings[:3],
                )
                continue

        # Check sibling names too
        sibling_matched = False
        for sib_eid in siblings:
            sib_state = hass.states.get(sib_eid)
            if sib_state:
                sib_fname = sib_state.attributes.get("friendly_name", "")
                atype = _classify_appliance(sib_eid, sib_fname)
                if atype:
                    found[eid] = _SensorState(
                        entity_id=eid,
                        friendly_name=sib_fname or fname or eid,
                        appliance=atype,
                        discovery_method="sibling_name",
                        sibling_entities=siblings,
                    )
                    _LOGGER.info(
                        "Appliance [sibling]: %s → %s (via sibling '%s')",
                        eid, atype.label, sib_eid,
                    )
                    sibling_matched = True
                    break
        if sibling_matched:
            continue

        # Method 3: Area-based inference
        area_name = _get_entity_area(hass, eid)
        if area_name:
            for area_kw, atype in _AREA_HINTS.items():
                if area_kw in area_name:
                    found[eid] = _SensorState(
                        entity_id=eid,
                        friendly_name=fname or eid,
                        appliance=atype,
                        discovery_method=f"area:{area_name}",
                        sibling_entities=siblings,
                    )
                    _LOGGER.info(
                        "Appliance [area]: %s → %s (area='%s')",
                        eid, atype.label, area_name,
                    )
                    break
            else:
                unidentified_power.append((eid, fname, siblings))
        else:
            unidentified_power.append((eid, fname, siblings))

    # Method 4: Track unidentified power sensors as GENERIC
    # They'll be fingerprinted on their first observed cycle
    for eid, fname, siblings in unidentified_power:
        # Skip sensors that are clearly not appliances (solar, grid, battery)
        skip_keywords = (
            "solar", "grid", "battery", "total", "sum", "net",
            "generation", "consumption", "export", "import",
            "hvac", "furnace", "heat_pump", "air_handler",
        )
        combined = (eid + " " + fname).lower()
        if any(kw in combined for kw in skip_keywords):
            continue

        # Only track if current reading suggests it could be an appliance
        # (skip always-on things like routers, fridges at baseline)
        state = hass.states.get(eid)
        if state:
            try:
                current_w = float(state.state)
                unit = (state.attributes.get("unit_of_measurement") or "").lower()
                if unit in ("kw",):
                    current_w *= 1000.0
                # Skip sensors consistently reading very low (always-on devices)
                # or very high (whole-house monitors)
                if current_w > 5000:
                    continue
            except (ValueError, TypeError):
                pass

        found[eid] = _SensorState(
            entity_id=eid,
            friendly_name=fname or eid,
            appliance=ApplianceType.GENERIC,
            discovery_method="unidentified",
            identified=False,
            sibling_entities=siblings,
        )
        _LOGGER.info(
            "Appliance [unidentified]: %s → tracking as GENERIC "
            "(will fingerprint on first cycle, fname='%s')",
            eid, fname,
        )

    return found


def _discover_native_appliances(hass: HomeAssistant) -> dict[str, _NativeAppliance]:
    """
    Find smart appliances that expose native status entities.
    E.g. Samsung washer with binary_sensor.washer_run_completed.
    """
    found = {}

    for domain in ("binary_sensor", "sensor"):
        for state in hass.states.async_all(domain):
            eid = state.entity_id
            fname = (state.attributes.get("friendly_name") or "").lower()

            for pattern, trigger, forced_type in _NATIVE_PATTERNS:
                if pattern not in eid.lower() and pattern not in fname:
                    continue

                # Determine appliance type from entity context
                atype = forced_type
                if not atype:
                    atype = _classify_appliance(eid, fname)
                if not atype:
                    # Check the parent device name
                    dev_name, _ = _get_device_siblings(hass, eid)
                    if dev_name:
                        atype = _classify_appliance("", dev_name)
                if not atype:
                    atype = ApplianceType.GENERIC

                dev_name, _ = _get_device_siblings(hass, eid)
                display_name = dev_name or fname or eid

                found[eid] = _NativeAppliance(
                    entity_id=eid,
                    device_name=display_name,
                    appliance=atype,
                    trigger_state=trigger,
                    last_state=state.state or "",
                )
                _LOGGER.info(
                    "Appliance [native]: %s → %s (trigger='%s', "
                    "device='%s')",
                    eid, atype.label, trigger, display_name,
                )
                break  # Don't match multiple patterns for same entity

    return found


def _discover_whole_home_meter(hass: HomeAssistant) -> Optional[_DeltaTracker]:
    """
    Find the main whole-home power consumption sensor for delta tracking.
    Looks for the primary Electric Consumption [W] entity — the one WITHOUT
    a circuit suffix like (1), (2).
    """
    candidates = []
    for state in hass.states.async_all("sensor"):
        dc = state.attributes.get("device_class", "")
        unit = (state.attributes.get("unit_of_measurement") or "").lower()
        fname = (state.attributes.get("friendly_name") or "").lower()
        eid = state.entity_id.lower()

        if dc != "power" and unit not in ("w", "kw"):
            continue

        # Look for whole-home indicators
        is_whole_home = False
        for kw in ("electric consumption", "home energy", "total consumption",
                    "main power", "whole house", "grid consumption",
                    "mains power"):
            if kw in fname or kw.replace(" ", "_") in eid:
                is_whole_home = True
                break

        if not is_whole_home:
            continue

        # Prefer the one WITHOUT circuit suffixes like (1), (2)
        import re as _re
        has_suffix = bool(_re.search(r"\(\d+\)$", fname.strip()))

        try:
            current_w = float(state.state)
            if unit == "kw":
                current_w *= 1000.0
        except (ValueError, TypeError):
            current_w = 0.0

        # Skip if reading is unreasonably low (might be a sub-circuit)
        candidates.append((state.entity_id, fname, current_w, has_suffix))

    if not candidates:
        return None

    # Prefer no-suffix, highest reading
    candidates.sort(key=lambda c: (c[3], -c[2]))
    best_eid, best_fname, current_w, _ = candidates[0]

    _LOGGER.info(
        "Appliance [whole-home]: %s (%.0fW) — will track deltas for "
        "appliance start/stop detection",
        best_eid, current_w,
    )
    return _DeltaTracker(
        entity_id=best_eid,
        baseline_w=current_w,
        last_reading=current_w,
    )


# ── State machine tick ──────────────────────────────────────────────────────

def _process_reading(sensor: _SensorState, power_w: float) -> Optional[str]:
    """
    Feed a power reading into the state machine.
    Returns the appliance label if a cycle just completed, else None.

    For unidentified sensors (GENERIC), tracks peak power and attempts
    fingerprint-based classification after the first observed cycle.
    """
    now = time.time()
    sensor.last_power = power_w
    a = sensor.appliance

    if sensor.phase == "idle":
        if power_w >= a.run_threshold:
            if sensor.run_start == 0.0:
                sensor.run_start = now
            elif (now - sensor.run_start) >= a.sustained_seconds:
                sensor.phase = "running"
                sensor.announced = False
                sensor.peak_power = power_w
                _LOGGER.info(
                    "Appliance %s (%s): IDLE → RUNNING (%.0fW for %ds)",
                    sensor.entity_id, a.label, power_w,
                    int(now - sensor.run_start),
                )
        else:
            sensor.run_start = 0.0

    elif sensor.phase == "running":
        # Track peak power for fingerprinting
        if power_w > sensor.peak_power:
            sensor.peak_power = power_w

        if power_w < a.idle_threshold:
            if sensor.settle_start == 0.0:
                sensor.settle_start = now
            elif (now - sensor.settle_start) >= a.settle_seconds:
                # Cycle complete
                sensor.phase = "idle"
                sensor.run_start = 0.0
                sensor.settle_start = 0.0

                # Fingerprint unidentified sensors
                if not sensor.identified:
                    result = _fingerprint_from_power(sensor.peak_power)
                    if result:
                        new_type, reason = result
                        _LOGGER.info(
                            "Appliance FINGERPRINT: %s peak=%.0fW → %s (%s)",
                            sensor.entity_id, sensor.peak_power,
                            new_type.label, reason,
                        )
                        sensor.appliance = new_type
                        sensor.identified = True
                        sensor.discovery_method = (
                            f"fingerprint:{sensor.peak_power:.0f}W"
                        )

                if not sensor.announced:
                    sensor.announced = True
                    label = sensor.appliance.label
                    _LOGGER.info(
                        "Appliance %s (%s): RUNNING → DONE (peak=%.0fW)",
                        sensor.entity_id, label, sensor.peak_power,
                    )
                    return label
        else:
            sensor.settle_start = 0.0

    return None


# ── Event handler ───────────────────────────────────────────────────────────

@callback
def _on_state_changed(event: Event) -> None:
    """Handle state changes for power sensors, native appliances, and whole-home delta."""
    if not _MON.running:
        return

    entity_id = event.data.get("entity_id", "")
    new_state = event.data.get("new_state")
    if new_state is None:
        return

    # ── Native appliance status change ──────────────────────────────
    if entity_id in _MON.natives:
        native = _MON.natives[entity_id]
        old_state_val = native.last_state
        new_state_val = new_state.state or ""
        native.last_state = new_state_val

        if (new_state_val == native.trigger_state
                and old_state_val != native.trigger_state
                and not native.announced):
            native.announced = True
            _LOGGER.info(
                "Native appliance DONE: %s (%s) — %s → %s",
                entity_id, native.appliance.label,
                old_state_val, new_state_val,
            )
            # Create a synthetic sensor state for _announce_done
            synth = _SensorState(
                entity_id=entity_id,
                friendly_name=native.device_name,
                appliance=native.appliance,
                discovery_method="native_status",
                peak_power=0.0,
            )
            _MON.hass.async_create_task(
                _announce_done(synth, native.appliance.label)
            )
        elif new_state_val != native.trigger_state:
            # Reset announced flag when appliance starts a new cycle
            native.announced = False
        return

    # ── Power sensor update ─────────────────────────────────────────
    if entity_id in _MON.sensors:
        try:
            raw = new_state.state
            if raw in ("unavailable", "unknown", ""):
                return
            power_w = float(raw)
            unit = (new_state.attributes.get("unit_of_measurement") or "").lower()
            if unit in ("kw",):
                power_w *= 1000.0
        except (ValueError, TypeError):
            return

        sensor = _MON.sensors[entity_id]
        completed = _process_reading(sensor, power_w)
        if completed:
            _MON.hass.async_create_task(_announce_done(sensor, completed))
        return

    # ── Whole-home delta tracking ───────────────────────────────────
    if _MON.delta and entity_id == _MON.delta.entity_id:
        try:
            raw = new_state.state
            if raw in ("unavailable", "unknown", ""):
                return
            power_w = float(raw)
            unit = (new_state.attributes.get("unit_of_measurement") or "").lower()
            if unit in ("kw",):
                power_w *= 1000.0
        except (ValueError, TypeError):
            return

        _process_delta(power_w)
        return


# ── Whole-home delta processing ─────────────────────────────────────────────

_DELTA_JUMP_THRESHOLD = 200   # W — minimum jump to register as appliance start
_DELTA_DROP_THRESHOLD = 200   # W — minimum drop to register as appliance stop
_DELTA_SUSTAIN_SECONDS = 30   # Must sustain the new level for this long
_DELTA_MAX_SAMPLES = 20       # Rolling baseline window


def _process_delta(power_w: float) -> None:
    """
    Track whole-home power for sudden jumps/drops indicating appliance
    start/stop. Uses a rolling baseline and detects significant deviations.
    """
    dt = _MON.delta
    if not dt:
        return

    now = time.time()
    dt.last_reading = power_w

    # Update rolling baseline (only when no active deltas)
    if not dt.active_deltas:
        dt.samples.append(power_w)
        if len(dt.samples) > _DELTA_MAX_SAMPLES:
            dt.samples = dt.samples[-_DELTA_MAX_SAMPLES:]
        dt.baseline_w = sum(dt.samples) / len(dt.samples)

    diff = power_w - dt.baseline_w

    # ── Detect new jump (appliance started) ─────────────────────────
    if diff > _DELTA_JUMP_THRESHOLD:
        already_tracked = False
        for did, dinfo in dt.active_deltas.items():
            if abs(dinfo["delta_w"] - diff) < 300:
                already_tracked = True
                if power_w > dinfo["peak_w"]:
                    dinfo["peak_w"] = power_w
                break

        if not already_tracked:
            did = dt.next_id()
            matched = None
            ambiguous = False
            if _MON.profile:
                matched, _err, ambiguous = _match_declared(diff)
                if matched:
                    ref_w = matched.get("learned_w") or matched.get("watts") or diff
                    guess = matched["name"]
                    reason = f"matches declared {matched['name']} (~{ref_w:.0f}W)"
                else:
                    guess = "unknown"
                    reason = "no declared appliance matches this load"
            else:
                result = _fingerprint_from_power(diff)
                guess = result[0].label if result else "unknown"
                reason = result[1] if result else "unrecognized power signature"
            dt.active_deltas[did] = {
                "start_time": now,
                "delta_w": diff,
                "peak_w": power_w,
                "appliance_guess": guess,
                "reason": reason,
                "matched": matched,
                "ambiguous": ambiguous,
                "announced": False,
            }
            _LOGGER.info(
                "Whole-home DELTA: +%.0fW (baseline=%.0fW, now=%.0fW) "
                "→ %s (%s)",
                diff, dt.baseline_w, power_w, guess, reason,
            )

    # ── Detect drop (appliance stopped) ─────────────────────────────
    to_remove = []
    for did, dinfo in dt.active_deltas.items():
        elapsed = now - dinfo["start_time"]
        if power_w < dt.baseline_w + _DELTA_DROP_THRESHOLD:
            if elapsed > _DELTA_SUSTAIN_SECONDS and not dinfo["announced"]:
                dinfo["announced"] = True
                guess = dinfo["appliance_guess"]
                delta_w = dinfo["delta_w"]
                peak_w = dinfo["peak_w"]
                matched = dinfo.get("matched")

                _LOGGER.info(
                    "Whole-home DELTA done: %s (delta=%.0fW, peak=%.0fW, "
                    "duration=%ds)",
                    guess, delta_w, peak_w, int(elapsed),
                )

                if matched:
                    # Confirmed appliance — confident name; refine its profile.
                    _learn_watts(matched, delta_w, dinfo.get("ambiguous", False))
                    atype = _type_to_appliance(matched["type"])
                    synth = _SensorState(
                        entity_id=dt.entity_id,
                        friendly_name=matched["name"],
                        appliance=atype,
                        discovery_method=f"whole_home_match:{delta_w:.0f}W",
                        peak_power=peak_w,
                    )
                    _MON.hass.async_create_task(
                        _announce_done(synth, matched["name"])
                    )
                elif _MON.profile:
                    # A profile is configured but nothing matched — do NOT claim a
                    # specific appliance (this is what caused wrong "washer done"
                    # calls). Optionally note an unidentified load.
                    if _MON.announce_unknown:
                        synth = _SensorState(
                            entity_id=dt.entity_id,
                            friendly_name="an appliance",
                            appliance=ApplianceType.GENERIC,
                            discovery_method=f"whole_home_unmatched:{delta_w:.0f}W",
                            peak_power=peak_w,
                        )
                        _MON.hass.async_create_task(
                            _announce_done(synth, f"an appliance (~{delta_w:.0f}W)")
                        )
                    else:
                        _LOGGER.info(
                            "Unmatched load (~%.0fW) finished — not announced "
                            "(no declared appliance matches)", delta_w,
                        )
                elif guess != "unknown":
                    # Legacy behaviour when no profile is configured at all.
                    result = _fingerprint_from_power(delta_w)
                    atype = result[0] if result else ApplianceType.GENERIC
                    synth = _SensorState(
                        entity_id=dt.entity_id,
                        friendly_name=f"Detected {guess.title()}",
                        appliance=atype,
                        discovery_method=f"whole_home_delta:{delta_w:.0f}W",
                        peak_power=peak_w,
                    )
                    _MON.hass.async_create_task(
                        _announce_done(synth, atype.label)
                    )
                to_remove.append(did)

        # Timeout after 4 hours
        if elapsed > 14400:
            to_remove.append(did)

    for did in to_remove:
        dt.active_deltas.pop(did, None)


async def _announce_done(sensor: _SensorState, appliance_label: str) -> None:
    """Announce appliance cycle completion through JARVIS audio pipeline."""
    hass = _MON.hass
    config = _MON.config
    honorific = config.get("honorific", "sir")
    nice_name = appliance_label.replace("_", " ").title()
    friendly = sensor.friendly_name or nice_name

    # Build the announcement message
    if appliance_label.lower() in friendly.lower():
        message = f"{honorific.title()}, the {nice_name} cycle is complete."
    elif "fingerprint" in sensor.discovery_method:
        # Auto-identified — mention what we think it is
        message = (
            f"{honorific.title()}, {friendly} appears to have finished "
            f"a cycle. Based on its power profile, it looks like a "
            f"{nice_name}."
        )
    else:
        message = (
            f"{honorific.title()}, {friendly} has finished its cycle. "
            f"The {nice_name} is done."
        )

    _LOGGER.info(
        "Appliance announcement: %s (peak=%.0fW, method=%s)",
        message, sensor.peak_power, sensor.discovery_method,
    )

    # Route through output gate
    from . import output_gate
    allowed, reason = output_gate.can_announce(
        entity_id=sensor.entity_id,
        category="appliance",
        urgency="medium",
        message=message,
    )
    if not allowed:
        _LOGGER.debug("Appliance announcement suppressed: %s", reason)
        output_gate.record_announcement(
            entity_id=sensor.entity_id, category="appliance",
            urgency="medium", message=message, was_spoken=False,
        )
        return

    # Check announcements_enabled
    from .const import DOMAIN
    announcements_on = True
    try:
        for eid, data in hass.data.get(DOMAIN, {}).items():
            if isinstance(data, dict):
                rc = data.get("runtime_config", {})
                if "announcements_enabled" in rc:
                    announcements_on = bool(rc["announcements_enabled"])
                    break
    except Exception:
        pass

    if not announcements_on:
        _LOGGER.debug("Appliance: announcements disabled, logging only")
        output_gate.record_announcement(
            entity_id=sensor.entity_id, category="appliance",
            urgency="medium", message=message, was_spoken=False,
        )
        return

    # Resolve TTS and speakers
    try:
        from .tts_helper import resolve_tts_for_context, async_announce
        from .audio_routing import observer_speak_target
        from . import sleep_detection

        bedroom_areas = config.get("bedroom_areas", []) or []
        sleeping, _ = sleep_detection.is_sleeping(
            hass,
            bedroom_area_ids=bedroom_areas,
            quiet_start=config.get("observer_quiet_start", "22:00"),
            quiet_end=config.get("observer_quiet_end", "07:00"),
        )

        broadcast_group = config.get("broadcast_group") or None

        # Read announcement_speakers from runtime_config
        ann_speakers = None
        try:
            import json as _json
            for eid, data in hass.data.get(DOMAIN, {}).items():
                if isinstance(data, dict):
                    rc = data.get("runtime_config", {})
                    raw = rc.get("announcement_speakers")
                    if raw:
                        parsed = _json.loads(raw) if isinstance(raw, str) else raw
                        if isinstance(parsed, list) and parsed:
                            ann_speakers = parsed
                            break
        except Exception:
            pass

        targets, mode = observer_speak_target(
            hass,
            urgency="medium",
            broadcast_group=broadcast_group,
            announcement_speakers=ann_speakers,
            is_sleeping=sleeping,
        )

        if mode in ("suppressed", "notify_only") or not targets:
            output_gate.record_announcement(
                entity_id=sensor.entity_id, category="appliance",
                urgency="medium", message=message, was_spoken=False,
            )
            if mode == "notify_only":
                # Try phone notification
                try:
                    notify_svc = config.get("notify_service", "")
                    if notify_svc:
                        svc_domain, svc_name = notify_svc.split(".", 1)
                        await hass.services.async_call(
                            svc_domain, svc_name,
                            {"message": message, "title": "JARVIS"},
                            blocking=False,
                        )
                except Exception:
                    pass
            return

        # Speak
        tts_entity = resolve_tts_for_context(
            hass, "sentinel",
            config.get("tts_engine", "auto"),
            config.get("tts_premium_engine") or None,
            config.get("tts_premium_contexts") or [],
        )
        if tts_entity and targets:
            await async_announce(
                hass, message, tts_entity, targets,
                context="appliance",
            )
            output_gate.record_announcement(
                entity_id=sensor.entity_id, category="appliance",
                urgency="medium", message=message, was_spoken=True,
            )
        else:
            output_gate.record_announcement(
                entity_id=sensor.entity_id, category="appliance",
                urgency="medium", message=message, was_spoken=False,
            )

    except Exception as exc:
        _LOGGER.warning("Appliance announce error: %s", exc)


# ── Start / stop ────────────────────────────────────────────────────────────

async def start(hass: HomeAssistant, config: dict) -> None:
    """Begin monitoring. Auto-discovers appliance power sensors."""
    if _MON.running:
        await stop()

    _MON.hass = hass

    # Always honor the latest panel-saved appliance profile. The observer starts
    # us at boot with a config built from entry.data/options only — which predates
    # anything saved from the Appliances panel — so without this the monitor runs
    # with an EMPTY profile and falls back to GUESSING appliances from the whole-
    # home meter (the "Washer cycle complete" false positives). runtime_config
    # holds the live panel values; the persisted config is the boot-time fallback.
    _prof_val = None
    _unknown_val = None
    try:
        from .const import DOMAIN as _DOM
        for _eid, _data in (hass.data.get(_DOM) or {}).items():
            if isinstance(_data, dict) and isinstance(_data.get("runtime_config"), dict):
                _rc = _data["runtime_config"]
                if "appliance_profile" in _rc:
                    _prof_val = _rc["appliance_profile"]
                if "appliance_announce_unknown" in _rc:
                    _unknown_val = _rc["appliance_announce_unknown"]
                break
    except Exception as _exc:
        _LOGGER.debug("Appliance profile runtime read note: %s", _exc)
    if _prof_val is None:
        try:
            from . import jarvis_config
            _persisted = await hass.async_add_executor_job(jarvis_config.get_all)
            if isinstance(_persisted, dict):
                if "appliance_profile" in _persisted:
                    _prof_val = _persisted["appliance_profile"]
                if _unknown_val is None and "appliance_announce_unknown" in _persisted:
                    _unknown_val = _persisted["appliance_announce_unknown"]
        except Exception as _exc:
            _LOGGER.debug("Appliance profile persisted read note: %s", _exc)
    config = dict(config)
    if _prof_val is not None:
        config["appliance_profile"] = _prof_val
    if _unknown_val is not None:
        config["appliance_announce_unknown"] = _unknown_val
    _MON.config = config

    # Discover sensors
    _MON.sensors = await hass.async_add_executor_job(_discover_sensors, hass)

    # Discover native smart appliance status entities
    _MON.natives = await hass.async_add_executor_job(
        _discover_native_appliances, hass,
    )

    # Discover whole-home energy meter for delta tracking
    _MON.delta = await hass.async_add_executor_job(
        _discover_whole_home_meter, hass,
    )

    # Also add any explicitly configured sensors
    explicit = config.get("appliance_sensors", {})
    if isinstance(explicit, dict):
        for sensor_id, label in explicit.items():
            if sensor_id not in _MON.sensors:
                atype = _classify_appliance(sensor_id, label) or ApplianceType.GENERIC
                state = hass.states.get(sensor_id)
                fname = ""
                if state:
                    fname = state.attributes.get("friendly_name", sensor_id)
                _, siblings = _get_device_siblings(hass, sensor_id)
                _MON.sensors[sensor_id] = _SensorState(
                    entity_id=sensor_id,
                    friendly_name=fname or label,
                    appliance=atype,
                    discovery_method="explicit_config",
                    identified=atype != ApplianceType.GENERIC,
                    sibling_entities=siblings,
                )
                _LOGGER.info(
                    "Appliance [explicit]: %s → %s",
                    sensor_id, atype.label,
                )

    # ── User-declared appliance profile (Settings → Appliances) ──────────────
    _MON.announce_unknown = bool(config.get("appliance_announce_unknown", False))
    _MON.profile = _normalize_profile(config)
    _MON.claimed = set()
    _MON.disagg = []
    for ap in _MON.profile:
        ent = ap.get("entity", "")
        if ent:
            # Dedicated entity is authoritative — track it directly and exclude
            # it from whole-home disaggregation.
            _MON.claimed.add(ent)
            st = hass.states.get(ent)
            fname = (st.attributes.get("friendly_name", ent) if st else ent) or ap["name"]
            atype = _type_to_appliance(ap["type"])
            unit = (st.attributes.get("unit_of_measurement", "") if st else "") or ""
            dclass = (st.attributes.get("device_class", "") if st else "") or ""
            is_power = (dclass == "power") or unit.lower() in ("w", "kw", "watt", "watts")
            if is_power and ent not in _MON.sensors:
                _, siblings = _get_device_siblings(hass, ent)
                _MON.sensors[ent] = _SensorState(
                    entity_id=ent, friendly_name=fname, appliance=atype,
                    discovery_method="declared_entity", identified=True,
                    sibling_entities=siblings,
                )
                _LOGGER.info("Appliance [declared/power]: %s → %s", ent, ap["name"])
            elif not is_power and ent not in _MON.natives:
                low = ent.lower()
                trigger = "off"
                for kw, ts, _t in _NATIVE_PATTERNS:
                    if kw in low:
                        trigger = ts
                        break
                else:
                    for cand in ("finished", "complete", "completed", "idle", "standby"):
                        # leave default 'off'; user can map a power sensor for precision
                        break
                _MON.natives[ent] = _NativeAppliance(
                    entity_id=ent, device_name=ap["name"], appliance=atype,
                    trigger_state=trigger,
                )
                _LOGGER.info("Appliance [declared/native]: %s → %s (done='%s')",
                             ent, ap["name"], trigger)
        elif ap.get("watts", 0) > 0:
            # No dedicated entity — becomes a whole-home disaggregation candidate.
            _MON.disagg.append(ap)
            _LOGGER.info("Appliance [declared/disagg]: %s (~%.0fW)",
                         ap["name"], ap["watts"])

    if not _MON.sensors and not _MON.natives and not _MON.delta:
        _LOGGER.info(
            "Appliance monitor: no power sensors found. Discovery methods "
            "tried: keyword match, device siblings, area inference, "
            "power fingerprinting. To enable: name a sensor with "
            "washer/dryer/dishwasher, place it in a 'laundry' area, or "
            "configure appliance_sensors explicitly."
        )
        return

    identified = sum(1 for s in _MON.sensors.values() if s.identified)
    unidentified = len(_MON.sensors) - identified

    _MON.running = True
    _MON.unsub = hass.bus.async_listen("state_changed", _on_state_changed)

    total = len(_MON.sensors) + len(_MON.natives) + (1 if _MON.delta else 0)
    identified = sum(1 for s in _MON.sensors.values() if s.identified)
    unidentified = len(_MON.sensors) - identified

    _LOGGER.info(
        "JARVIS Appliance Monitor v5.7.05 started — %d source(s): "
        "%d power sensors (%d identified, %d pending), "
        "%d native appliances, %s",
        total, len(_MON.sensors), identified, unidentified,
        len(_MON.natives),
        f"whole-home delta ({_MON.delta.entity_id})" if _MON.delta else "no whole-home meter",
    )
    for s in _MON.sensors.values():
        _LOGGER.info(
            "  • [power] %s → %s [%s] %s",
            s.entity_id, s.appliance.label, s.discovery_method,
            "(unidentified)" if not s.identified else "",
        )
    for n in _MON.natives.values():
        _LOGGER.info(
            "  • [native] %s → %s (trigger='%s', device='%s')",
            n.entity_id, n.appliance.label, n.trigger_state, n.device_name,
        )
    if _MON.delta:
        _LOGGER.info(
            "  • [delta] %s — baseline %.0fW",
            _MON.delta.entity_id, _MON.delta.baseline_w,
        )


async def stop() -> None:
    """Stop monitoring."""
    if _MON.unsub:
        try:
            _MON.unsub()
        except Exception:
            pass
    _MON.sensors.clear()
    _MON.natives.clear()
    _MON.delta = None
    _MON.running = False
    _LOGGER.info("JARVIS Appliance Monitor stopped")


def is_running() -> bool:
    return _MON.running


def status() -> dict:
    """Return current state for diagnostics."""
    return {
        "running": _MON.running,
        "profile": [
            {
                "name": ap["name"], "type": ap["type"],
                "entity": ap.get("entity", ""),
                "watts": ap.get("watts", 0),
                "learned_w": round(ap.get("learned_w", ap.get("watts", 0)) or 0),
                "observed_n": ap.get("observed_n", 0),
                "mode": "entity" if ap.get("entity") else (
                    "disaggregation" if ap.get("watts", 0) > 0 else "declared"),
            }
            for ap in _MON.profile
        ],
        "sensors": {
            eid: {
                "appliance": s.appliance.label,
                "phase": s.phase,
                "power_w": s.last_power,
                "peak_w": s.peak_power,
                "friendly_name": s.friendly_name,
                "discovery": s.discovery_method,
                "identified": s.identified,
                "siblings": s.sibling_entities[:5],
            }
            for eid, s in _MON.sensors.items()
        },
        "native_appliances": {
            eid: {
                "appliance": n.appliance.label,
                "device_name": n.device_name,
                "trigger_state": n.trigger_state,
                "current_state": n.last_state,
                "announced": n.announced,
            }
            for eid, n in _MON.natives.items()
        },
        "whole_home_delta": {
            "entity_id": _MON.delta.entity_id,
            "baseline_w": _MON.delta.baseline_w,
            "last_reading_w": _MON.delta.last_reading,
            "active_events": len(_MON.delta.active_deltas),
            "deltas": {
                did: {
                    "delta_w": d["delta_w"],
                    "peak_w": d["peak_w"],
                    "guess": d["appliance_guess"],
                    "duration_s": int(time.time() - d["start_time"]),
                }
                for did, d in _MON.delta.active_deltas.items()
            },
        } if _MON.delta else None,
    }
