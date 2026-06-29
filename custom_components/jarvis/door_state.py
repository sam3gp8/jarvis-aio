"""
Door open/closed state for the Residence 3D model.

Resolves the home's doors into the model's fixed door slots
(front · garage · garage_rear · kitchen_garage · cellar · basement). An explicit
mapping (slot -> entity_id, configured on the Residence tab) is honoured first
and removes all guessing; any slot without an explicit entity falls back to
auto-detection by device_class + name keywords. Kept dependency-light (no HA
component imports) so it's unit-testable in isolation.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant

# Must match the panel model's dOf() door keys.
DOOR_SLOTS = ("front", "garage", "garage_rear", "kitchen_garage", "cellar", "basement")

# Cover device_classes that are window coverings, not doors — excluded when
# accepting covers that carry no explicit door device_class.
NON_DOOR_COVER_CLASSES = ("shade", "shutter", "blind", "curtain",
                          "awning", "window", "damper")

_DOOR_BINARY_CLASSES = ("door", "garage_door", "opening")
_DOOR_COVER_CLASSES = ("garage", "door", "gate")


def entity_is_open(state_obj) -> bool:
    """Is an explicitly-mapped door entity 'open'? Works across domains so a
    cover, a door/contact binary_sensor, a lock, or a switch can all be mapped."""
    dom = state_obj.entity_id.split(".")[0]
    s = str(state_obj.state).lower()
    if s in ("unknown", "unavailable", ""):
        return False
    if dom == "cover":
        return s not in ("closed", "closing")
    if dom == "lock":
        return s in ("unlocked", "open", "opening", "jammed")
    if dom in ("binary_sensor", "switch", "input_boolean", "light"):
        return s == "on"
    return s in ("open", "opening", "on", "true", "unlocked")


def classify(eid: str, name: str) -> str:
    """Best-guess door slot for an entity, by id/name keywords. '' if no match."""
    s = (eid + " " + (name or "")).lower()
    if any(k in s for k in ("cellar", "bulkhead", "hatch")):
        return "cellar"
    if "basement" in s:
        return "basement"
    if "garage" in s and any(k in s for k in ("kitchen", "interior", "inside", "house", "mud")):
        return "kitchen_garage"
    if "kitchen" in s and "garage" in s:
        return "kitchen_garage"
    if "garage" in s and any(k in s for k in ("man", "side", "rear", "back", "walk", "person", "entry", "people")):
        return "garage_rear"
    if "garage" in s:
        return "garage"
    if "front" in s and "garage" not in s:
        return "front"
    return ""


def get_door_states(hass: HomeAssistant, mapping: dict | None = None) -> dict:
    """
    Resolve door slots to 'open'/'closed'. `mapping` is the explicit
    {slot: entity_id} config (read by the caller). Explicit entries win; the
    rest auto-detect. Covers with no device_class are accepted when their name
    clearly indicates a door and they aren't a window covering — the common case
    for garage doors exposed without a device_class. OPEN wins on ties. Missing
    slots are simply absent (the model treats them as closed). Never raises.
    """
    try:
        keys: dict[str, str] = {}

        def consider(key: str, is_open: bool) -> None:
            if not key:
                return
            if key not in keys or is_open:
                keys[key] = "open" if is_open else "closed"

        # 1) Explicit mapping wins — no keyword guessing for these slots.
        explicit: set[str] = set()
        if isinstance(mapping, dict):
            for slot, eid in mapping.items():
                if slot not in DOOR_SLOTS or not eid:
                    continue
                st = hass.states.get(eid)
                if st is None:
                    continue
                keys[slot] = "open" if entity_is_open(st) else "closed"
                explicit.add(slot)

        # 2) Auto-detect everything not explicitly mapped.
        for st in hass.states.async_all("binary_sensor"):
            if st.attributes.get("device_class") not in _DOOR_BINARY_CLASSES:
                continue
            slot = classify(st.entity_id, st.attributes.get("friendly_name", ""))
            if slot in explicit:
                continue
            consider(slot, str(st.state).lower() == "on")

        for st in hass.states.async_all("cover"):
            dc = st.attributes.get("device_class")
            slot = classify(st.entity_id, st.attributes.get("friendly_name", ""))
            if dc not in _DOOR_COVER_CLASSES:
                # accept name-classified covers that aren't window coverings
                if not slot or dc in NON_DOOR_COVER_CLASSES:
                    continue
            if slot in explicit:
                continue
            consider(slot, str(st.state).lower() not in ("closed", "closing"))

        return keys
    except Exception:
        return {}
