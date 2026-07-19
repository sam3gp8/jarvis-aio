"""
Outdoor classification — the single source of truth for "is this entity part of
the outside world?"

Why it matters: the intrusion investigator must reason about the *inside* of the
house. An outdoor motion sensor seeding an investigation, or a delivery driver
on the driveway camera *confirming* one, is exactly how false alarms happen.
Before this module, outdoor knowledge lived in two unconnected fragments (a
six-keyword skip inside the motion scan, and a never-called notable-event
filter); everything now asks here.

Classification is layered, most-authoritative first:

  1. Explicit config (the user's word is final):
       indoor_entities   — globs; force-classify as indoor (wins over everything)
       outdoor_entities  — globs; force-classify as outdoor
  2. The entity's Home Assistant *area*, matched against outdoor area names
     (defaults below, extendable via `outdoor_areas` config).
  3. Name keywords in the entity_id / friendly name — word-bounded, so
     'gate' matches cover.side_gate but never cover.garage_door, and
     'front_yard' matches while a front-door contact sensor does not.

Also home to the notable-outdoor-event policy (person/package/mail/damage are
worth surfacing; generic motion, passing cars, and animals are not) that the
vision layer consults. Dependency-light; registry lookups are guarded so this
works everywhere, including tests. Never raises.
"""
from __future__ import annotations

import fnmatch
import re
from typing import Optional

# Area names (slugged) considered outdoor by default. Users extend via the
# `outdoor_areas` config list.
DEFAULT_OUTDOOR_AREAS = {
    "backyard", "back_yard", "front_yard", "front_door", "driveway", "patio",
    "porch", "side_yard", "deck", "garden", "yard", "pool", "exterior",
    "outside", "outdoor", "outdoors", "shed", "carport", "walkway", "sidewalk",
    "lawn", "curb", "gazebo",
}

# Word-bounded name keywords. Deliberate exclusions: 'front'/'door' alone
# (breach contact sensors like binary_sensor.front_door are the *house
# envelope*, not the yard) and 'garage' (the garage is part of the envelope —
# its door is a breach point, not scenery).
_NAME_RE = re.compile(
    r"(?:^|[._\s-])(outdoor|outside|exterior|backyard|back_?yard|front_?yard|"
    r"side_?yard|yard|garden|driveway|porch|patio|deck|shed|gazebo|carport|"
    r"curb|street|sidewalk|walkway|lawn|pool|doorbell|mailbox|gate|fence)"
    r"(?:$|[._\s-])", re.I)

# What kinds of outdoor detections are worth telling the user about.
NOTABLE_OUTDOOR = {
    "person": True,       # someone on the property — always notable
    "package": True,
    "mail": True,
    "damage": True,
    "vehicle": False,     # cars come and go; not notable by default
    "animal": False,
    "motion": False,      # wind, shadows, passing traffic
}


def _cfg_list(key: str) -> list[str]:
    """A config value as a lowercase list (accepts list or JSON string)."""
    try:
        from . import jarvis_config
        val = jarvis_config.get(key)
        if isinstance(val, str) and val.strip():
            import json
            val = json.loads(val)
        if isinstance(val, (list, tuple)):
            return [str(v).lower().strip() for v in val if str(v).strip()]
    except Exception:
        pass
    return []


def _matches_any(eid: str, fname: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(eid, p) or fnmatch.fnmatch(fname, p)
               for p in patterns)


def _area_slug(hass, entity_id: str) -> str:
    """The entity's HA area name, slugged. '' when unknown (guarded)."""
    if hass is None:
        return ""
    try:
        from homeassistant.helpers import (
            entity_registry as er, device_registry as dr, area_registry as ar,
        )
        ent = er.async_get(hass).async_get(entity_id)
        area_id = None
        if ent:
            area_id = ent.area_id
            if not area_id and ent.device_id:
                dev = dr.async_get(hass).async_get(ent.device_id)
                area_id = dev.area_id if dev else None
        if not area_id:
            return ""
        area = ar.async_get(hass).async_get_area(area_id)
        name = (area.name if area else area_id) or ""
        return "_".join(str(name).strip().lower().split())
    except Exception:
        return ""


def is_outdoor(hass, entity_id: str, friendly_name: Optional[str] = None) -> bool:
    """Is this entity part of the outside world? Layered: explicit config →
    HA area → name keywords. Defaults to indoor when nothing matches."""
    eid = (entity_id or "").lower()
    fname = (friendly_name or "").lower()
    if not fname and hass is not None:
        st = hass.states.get(entity_id)
        if st is not None:
            fname = str(st.attributes.get("friendly_name") or "").lower()

    if _matches_any(eid, fname, _cfg_list("indoor_entities")):
        return False                      # the user's word is final
    if _matches_any(eid, fname, _cfg_list("outdoor_entities")):
        return True

    area = _area_slug(hass, entity_id)
    if area and area in (DEFAULT_OUTDOOR_AREAS | set(_cfg_list("outdoor_areas"))):
        return True

    return bool(_NAME_RE.search(eid) or _NAME_RE.search(fname))


def location_mode(entity_id: str, indoor: list[str], outdoor: list[str]) -> str:
    """The explicit designation for this exact entity: 'indoor', 'outdoor',
    or 'auto' (heuristics decide). Exact-id matches only — globs a user wrote
    by hand still classify via is_outdoor, but read as 'auto' here since they
    aren't a per-camera pin (v6.49.0)."""
    eid = (entity_id or "").lower()
    if eid in [str(x).lower() for x in (indoor or [])]:
        return "indoor"
    if eid in [str(x).lower() for x in (outdoor or [])]:
        return "outdoor"
    return "auto"


def set_entity_location(indoor: list[str], outdoor: list[str], entity_id: str,
                        mode: str) -> tuple[list[str], list[str]]:
    """Pure pin/unpin of one entity across the two designation lists.
    'indoor'/'outdoor' pins the exact id into that list and out of the other;
    'auto' removes it from both (heuristics resume). Globs and other entries
    are preserved untouched. Returns new lists; callers persist."""
    eid = (entity_id or "").lower().strip()
    keep = lambda lst: [x for x in (lst or []) if str(x).lower() != eid]  # noqa: E731
    new_in, new_out = keep(indoor), keep(outdoor)
    if mode == "indoor":
        new_in.append(eid)
    elif mode == "outdoor":
        new_out.append(eid)
    return new_in, new_out


def notable(hass, entity_id: str, detection_type: str = "motion",
            area_name: Optional[str] = None) -> bool:
    """Is an *outdoor* event worth surfacing? Non-outdoor entities return False
    here — indoor events are the safety manager's business, not this filter's."""
    if area_name is not None:
        slug = "_".join(str(area_name).strip().lower().split())
        outdoor_here = slug in (DEFAULT_OUTDOOR_AREAS | set(_cfg_list("outdoor_areas")))
    else:
        outdoor_here = is_outdoor(hass, entity_id)
    if not outdoor_here:
        return False
    return bool(NOTABLE_OUTDOOR.get(str(detection_type).lower(), False))
